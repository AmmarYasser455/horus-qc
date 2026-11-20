"""Horus QC check engine.

Pure PyQGIS and no UI, so it can run inside a QgsTask worker thread.
Geometries are measured in a metric CRS (the layer CRS if it is already in
metres, otherwise the local UTM zone), so thresholds in metres and m² mean the
same thing on EPSG:4326 data as on projected data.
"""

from dataclasses import dataclass, field

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransformContext,
    QgsGeometry,
    QgsRectangle,
    QgsSpatialIndex,
)

from ..compat import GEOS_VALIDATOR
from .catalog import CHECKS, SEVERITY_RANK
from .geomutils import (
    corner_report,
    endpoints,
    expand,
    line_parts,
    part_count,
    point_parts,
    polygon_parts,
    polygonal,
    safe_point,
    self_crossing_point,
    transformer,
)


class Cancelled(Exception):
    pass


@dataclass
class LayerData:
    layer_id: str
    name: str
    crs: QgsCoordinateReferenceSystem
    geoms: dict  # fid -> QgsGeometry in layer CRS (scope + neighbours)
    scope: set  # fids errors are reported for


@dataclass
class QcError:
    check: str
    layer_id: str
    layer_name: str
    fid: int
    geometry: QgsGeometry  # marker geometry, in the layer CRS
    message: str
    fid2: object = None
    other_layer_id: str = ""
    value: object = None
    extra: dict = field(default_factory=dict)
    status: str = "open"  # open | fixed | ignored

    @property
    def definition(self):
        return CHECKS[self.check]

    @property
    def severity(self):
        return CHECKS[self.check].severity

    @property
    def key(self):
        return "%s|%s|%s|%s" % (self.check, self.layer_id, self.fid, self.fid2)


@dataclass
class Job:
    settings: object
    enabled: set
    context: QgsCoordinateTransformContext
    metric_crs: QgsCoordinateReferenceSystem
    buildings: LayerData = None
    roads: LayerData = None
    grid_crs: QgsCoordinateReferenceSystem = None
    grid_geoms: list = field(default_factory=list)  # grid CRS
    edge_geoms: list = field(default_factory=list)  # grid_crs, where the data is cut
    ignored: set = field(default_factory=set)


# --------------------------------------------------------------------------- run
def run_job(job, progress=None, is_cancelled=None):
    """Run every enabled check and return a sorted list of QcError."""

    def tick(frac, msg=""):
        if is_cancelled is not None and is_cancelled():
            raise Cancelled()
        if progress is not None:
            progress(max(0.0, min(100.0, frac * 100.0)), msg)

    metric = job.metric_crs

    def metric_geoms(ld):
        tx = transformer(ld.crs, metric, job.context)
        out = {}
        for fid, g in ld.geoms.items():
            out[fid] = tx(g) if g is not None else None
        return out

    grids_m = []
    edge_m = None
    if job.grid_geoms:
        tx = transformer(job.grid_crs, metric, job.context)
        for g in job.grid_geoms:
            gm = tx(g)
            if gm.isNull() or gm.isEmpty():
                continue
            if not gm.isGeosValid():
                gm = polygonal(gm.makeValid())
            grids_m.append(gm)
    if job.edge_geoms:
        tx = transformer(job.grid_crs, metric, job.context)
        edges = [tx(g) for g in job.edge_geoms]
        edges = [e if e.isGeosValid() else e.makeValid() for e in edges if not e.isNull()]
        if edges:
            union = QgsGeometry.unaryUnion(edges)
            if not union.isNull():
                edge_m = QgsGeometry(union.constGet().boundary())

    errors = []
    b_m = metric_geoms(job.buildings) if job.buildings else None
    r_m = metric_geoms(job.roads) if job.roads else None
    tick(0.05, "Prepared geometries")

    if job.buildings is not None:
        errors += _building_checks(job, b_m, r_m, grids_m, lambda f, m="": tick(0.05 + 0.6 * f, m))
    if job.roads is not None:
        errors += _road_checks(job, r_m, edge_m, lambda f, m="": tick(0.65 + 0.35 * f, m))

    for e in errors:
        if e.key in job.ignored:
            e.status = "ignored"
    errors.sort(key=lambda e: (SEVERITY_RANK[e.severity], e.check, e.layer_name, e.fid))
    tick(1.0, "Done")
    return errors


class _Emitter:
    def __init__(self, job, ld):
        self.job = job
        self.ld = ld
        self.back = transformer(job.metric_crs, ld.crs, job.context)
        self.out = []

    def __call__(self, check, fid, geom_m, msg, fid2=None, other="", value=None, extra=None):
        if check not in self.job.enabled:
            return
        g = self.back(geom_m) if geom_m is not None and not geom_m.isNull() else QgsGeometry()
        self.out.append(
            QcError(check, self.ld.layer_id, self.ld.name, fid, g, msg, fid2, other, value, extra or {})
        )


def _usable_polygons(gm):
    """fid -> valid polygonal metric geometry (invalid ones repaired, nulls dropped)."""
    out = {}
    for fid, g in gm.items():
        if g is None or g.isNull() or g.isEmpty():
            continue
        if not g.isGeosValid():
            g = polygonal(g.makeValid())
            if g.isNull() or g.isEmpty():
                continue
        out[fid] = g
    return out


def _index(geoms):
    idx = QgsSpatialIndex()
    for fid, g in geoms.items():
        idx.addFeature(fid, g.boundingBox())
    return idx


# --------------------------------------------------------------------------- buildings
def _building_checks(job, gm, roads_m, grids_m, tick):
    s = job.settings
    en = job.enabled
    ld = job.buildings
    add = _Emitter(job, ld)
    scope = sorted(f for f in ld.scope if f in gm)
    n = max(1, len(scope))

    # ---- single-feature checks
    for i, fid in enumerate(scope):
        if i % 250 == 0:
            tick(0.3 * i / n, "Checking building %d/%d" % (i + 1, n))
        g = gm[fid]
        if g is None or g.isNull() or g.isEmpty():
            add("b_null", fid, None, "Feature has no geometry")
            continue
        valid = g.isGeosValid()
        if not valid:
            errs = g.validateGeometry(GEOS_VALIDATOR)
            what = errs[0].what() if errs else "Invalid geometry"
            where = (
                QgsGeometry.fromPointXY(errs[0].where())
                if errs and errs[0].hasWhere()
                else safe_point(g)
            )
            key = "b_self_intersection" if "intersect" in what.lower() else "b_invalid"
            add(key, fid, where, what)
        parts = part_count(g)
        if parts >= 2:
            polys = sorted(polygon_parts(g), key=lambda p: p.area())
            marker = safe_point(polys[0]) if polys else safe_point(g)
            add("b_multipart", fid, marker, "%d separate polygons in one feature" % parts, value=parts)
        area = g.area() if valid else polygonal(g.makeValid()).area()
        if area < s.min_area_m2:
            add("b_tiny", fid, g, "Area %.1f m² (minimum %g m²)" % (area, s.min_area_m2), value=area)
        elif area > s.max_area_m2:
            add("b_huge", fid, g, "Area %.0f m² (maximum %g m²)" % (area, s.max_area_m2), value=area)
        if valid and ("b_spike" in en or "b_not_square" in en):
            spikes, wobbly = corner_report(g, s.spike_angle_deg, s.square_min_dev_deg, s.square_max_dev_deg)
            if spikes:
                add("b_spike", fid, QgsGeometry.fromMultiPointXY(spikes),
                    "%d spike vertex(es) sharper than %g°" % (len(spikes), s.spike_angle_deg),
                    value=len(spikes))
            elif wobbly:
                add("b_not_square", fid, QgsGeometry.fromMultiPointXY(wobbly),
                    "%d corner(s) %g–%g° off square" % (len(wobbly), s.square_min_dev_deg, s.square_max_dev_deg),
                    value=len(wobbly))

    usable = _usable_polygons(gm)
    in_scope = ld.scope

    def owner(preferred, other):
        """Report on ``preferred`` unless it is outside the scope."""
        if preferred in in_scope or other not in in_scope:
            return preferred, other
        return other, preferred

    # ---- building vs building
    pair_keys = ("b_duplicate", "b_contained", "b_overlap", "b_shared_wall", "b_gap")
    if any(k in en for k in pair_keys) and len(usable) > 1:
        idx = _index(usable)
        areas = {fid: g.area() for fid, g in usable.items()}
        tol = s.gap_tolerance_m if "b_gap" in en else 0.0
        for i, fid in enumerate(scope):
            if i % 250 == 0:
                tick(0.3 + 0.35 * i / n, "Comparing neighbours %d/%d" % (i + 1, n))
            g = usable.get(fid)
            if g is None:
                continue
            eng = QgsGeometry.createGeometryEngine(g.constGet())
            eng.prepareGeometry()
            for cid in idx.intersects(expand(g.boundingBox(), tol)):
                if cid == fid or (cid in in_scope and cid < fid):
                    continue
                h = usable[cid]
                if eng.intersects(h.constGet()):
                    inter = polygonal(g.intersection(h))
                    ia = inter.area() if not inter.isNull() else 0.0
                    if ia < s.min_overlap_m2:
                        continue  # just touching: a clean shared wall
                    amin = min(areas[fid], areas[cid]) or 1e-9
                    amax = max(areas[fid], areas[cid]) or 1e-9
                    small, big = (fid, cid) if areas[fid] <= areas[cid] else (cid, fid)
                    if ia >= s.duplicate_ratio * amax:
                        t, o = owner(max(fid, cid), min(fid, cid))
                        add("b_duplicate", t, inter, "Duplicate of building %s (%.0f%% identical)" % (o, 100 * ia / amax),
                            fid2=o, value=ia)
                    elif ia >= s.contained_ratio * amin:
                        add("b_contained", small, inter, "Lies inside building %s" % big, fid2=big, value=ia)
                    elif ia >= s.partial_ratio * amin:
                        t, o = owner(small, big)
                        add("b_overlap", t, inter,
                            "Overlaps building %s by %.1f m² (%.0f%% of the smaller)" % (o, ia, 100 * ia / amin),
                            fid2=o, value=ia)
                    else:
                        t, o = owner(small, big)
                        add("b_shared_wall", t, inter, "%.2f m² sliver shared with building %s" % (ia, o),
                            fid2=o, value=ia)
                elif tol > 0:
                    d = g.distance(h)
                    if 0.0 < d <= tol:
                        small = fid if areas[fid] <= areas[cid] else cid
                        t, o = owner(small, cid if small == fid else fid)
                        line = usable[t].shortestLine(usable[o])
                        add("b_gap", t, line, "%.2f m gap to building %s" % (d, o), fid2=o, value=d)

    # ---- building vs road
    if roads_m and ("b_on_road" in en or "b_road_setback" in en):
        roads = {fid: g for fid, g in roads_m.items() if g is not None and not g.isNull() and not g.isEmpty()}
        ridx = _index(roads)
        bufs = {}
        rb = max(s.road_buffer_m, 0.01)
        setback = s.road_setback_m if "b_road_setback" in en else 0.0
        reach = max(rb, setback)
        road_layer = job.roads.layer_id if job.roads else ""
        for i, fid in enumerate(scope):
            if i % 250 == 0:
                tick(0.65 + 0.2 * i / n, "Buildings vs roads %d/%d" % (i + 1, n))
            g = usable.get(fid)
            if g is None:
                continue
            best = (0.0, None, None)
            near = (None, None)
            for rid in ridx.intersects(expand(g.boundingBox(), reach)):
                rg = roads[rid]
                d = g.distance(rg)
                if d < rb:
                    if rid not in bufs:
                        bufs[rid] = rg.buffer(rb, 6)
                    inter = polygonal(g.intersection(bufs[rid]))
                    a = inter.area() if not inter.isNull() else 0.0
                    if a > best[0]:
                        best = (a, rid, inter)
                elif setback and d < setback and (near[0] is None or d < near[0]):
                    near = (d, rid)
            if best[1] is not None and best[0] >= s.road_min_overlap_m2:
                add("b_on_road", fid, best[2],
                    "%.1f m² inside the corridor of road %s (±%g m)" % (best[0], best[1], s.road_buffer_m),
                    fid2=best[1], other=road_layer, value=best[0])
            elif near[1] is not None:
                add("b_road_setback", fid, g.shortestLine(roads[near[1]]),
                    "%.1f m from road %s (set-back %g m)" % (near[0], near[1], setback),
                    fid2=near[1], other=road_layer, value=near[0])

    # ---- building vs grid
    if grids_m and ("b_crosses_grid" in en or "b_outside_grid" in en):
        gidx = _index({i: g for i, g in enumerate(grids_m)})
        for i, fid in enumerate(scope):
            if i % 250 == 0:
                tick(0.85 + 0.15 * i / n, "Buildings vs grid %d/%d" % (i + 1, n))
            g = usable.get(fid)
            if g is None:
                continue
            cells = [grids_m[k] for k in gidx.intersects(g.boundingBox())]
            touching = [c for c in cells if c.intersects(g)]
            if any(c.contains(g) for c in touching):
                continue
            if not touching:
                add("b_outside_grid", fid, g, "Not inside any grid cell")
                continue
            centre = safe_point(g)
            home = next((c for c in touching if c.intersects(centre)), None)
            rest = g.difference(home if home is not None else QgsGeometry.unaryUnion(touching))
            rest = polygonal(rest)
            out_area = rest.area() if not rest.isNull() else 0.0
            if out_area <= 0.01:
                continue
            add("b_crosses_grid", fid, rest,
                "%.1f m² (%.0f%%) lies outside its grid cell" % (out_area, 100 * out_area / max(g.area(), 1e-9)),
                value=out_area)
    tick(1.0)
    return add.out


# --------------------------------------------------------------------------- roads
def _road_checks(job, rm, edge_m, tick):
    s = job.settings
    en = job.enabled
    ld = job.roads
    add = _Emitter(job, ld)
    scope = sorted(f for f in ld.scope if f in rm)
    n = max(1, len(scope))
    tol = s.connect_tolerance_m

    for i, fid in enumerate(scope):
        if i % 250 == 0:
            tick(0.25 * i / n, "Checking road %d/%d" % (i + 1, n))
        g = rm[fid]
        if g is None or g.isNull() or g.isEmpty():
            add("r_null", fid, None, "Feature has no geometry")
            continue
        if not g.isGeosValid():
            errs = g.validateGeometry(GEOS_VALIDATOR)
            what = errs[0].what() if errs else "Invalid geometry"
            where = QgsGeometry.fromPointXY(errs[0].where()) if errs and errs[0].hasWhere() else safe_point(g)
            add("r_invalid", fid, where, what)
        parts = part_count(g)
        if parts >= 2:
            add("r_multipart", fid, g, "%d separate lines in one feature" % parts, value=parts)
        length = g.length()
        if length < s.min_road_length_m:
            add("r_short", fid, g, "%.2f m long (minimum %g m)" % (length, s.min_road_length_m), value=length)
        if "r_self_intersection" in en and not g.isSimple():
            ip = self_crossing_point(g)
            marker = QgsGeometry.fromPointXY(ip) if ip is not None else safe_point(g)
            add("r_self_intersection", fid, marker, "The line crosses itself")

    usable = {
        fid: g for fid, g in rm.items()
        if g is not None and not g.isNull() and not g.isEmpty() and g.length() > 0
    }
    if not usable:
        return add.out
    idx = _index(usable)
    lengths = {fid: g.length() for fid, g in usable.items()}
    in_scope = ld.scope

    # ---- duplicate / overlapping roads
    if "r_duplicate" in en or "r_overlap" in en:
        for i, fid in enumerate(scope):
            if i % 250 == 0:
                tick(0.25 + 0.25 * i / n, "Comparing roads %d/%d" % (i + 1, n))
            g = usable.get(fid)
            if g is None:
                continue
            for cid in idx.intersects(expand(g.boundingBox(), tol)):
                if cid == fid or (cid in in_scope and cid < fid):
                    continue
                h = usable[cid]
                if not g.intersects(h):
                    continue
                shared_parts = line_parts(g.intersection(h))
                shared = sum(p.length() for p in shared_parts)
                if shared < s.road_overlap_min_m:
                    continue
                shared_geom = QgsGeometry.collectGeometry(shared_parts)
                if shared >= 0.9 * min(lengths[fid], lengths[cid]):
                    if lengths[fid] == lengths[cid]:
                        t, o = max(fid, cid), min(fid, cid)
                    else:
                        t, o = (fid, cid) if lengths[fid] < lengths[cid] else (cid, fid)
                    if t not in in_scope and o in in_scope:
                        t, o = o, t
                    add("r_duplicate", t, shared_geom, "Runs on top of road %s for %.1f m" % (o, shared),
                        fid2=o, value=shared)
                else:
                    t, o = (fid, cid) if fid in in_scope else (cid, fid)
                    add("r_overlap", t, shared_geom, "Shares %.1f m with road %s" % (shared, o), fid2=o, value=shared)

    # ---- dangling ends: overshoot, undershoot, dead end
    end_keys = ("r_undershoot", "r_overshoot", "r_dead_end")
    if any(k in en for k in end_keys):
        reach = max(s.undershoot_m, s.overshoot_m, tol)
        for i, fid in enumerate(scope):
            if i % 250 == 0:
                tick(0.5 + 0.3 * i / n, "Road ends %d/%d" % (i + 1, n))
            g = usable.get(fid)
            if g is None:
                continue
            parts = line_parts(g)
            for pi, which, p in endpoints(g):
                pg = QgsGeometry.fromPointXY(p)
                rect = expand(QgsRectangle(p.x(), p.y(), p.x(), p.y()), reach)
                cands = [c for c in idx.intersects(rect) if c != fid]
                dists = {c: usable[c].distance(pg) for c in cands}
                if any(d <= tol for d in dists.values()):
                    continue
                # the end may touch another part of the same feature
                others = [q for k, q in enumerate(parts) if k != pi]
                if any(q.distance(pg) <= tol for q in others):
                    continue
                part = parts[pi]
                plen = part.length()
                at = 0.0 if which == "start" else plen
                best = None
                for c in cands:
                    for q in point_parts(part.intersection(usable[c])):
                        along = part.lineLocatePoint(q)
                        tail = abs(at - along)
                        if tol < tail <= s.overshoot_m and (best is None or tail < best[0]):
                            best = (tail, c, q, along)
                if best is not None:
                    tail, c, q, along = best
                    a, b = (0.0, along) if which == "start" else (along, plen)
                    piece = QgsGeometry(part.constGet().curveSubstring(a, b))
                    add("r_overshoot", fid, piece, "%.1f m tail past road %s" % (tail, c), fid2=c, value=tail,
                        extra={"part": pi, "end": which, "cross": add.back(q).asPoint()})
                    continue
                if dists:
                    c, d = min(dists.items(), key=lambda kv: kv[1])
                    if d <= s.undershoot_m:
                        add("r_undershoot", fid, pg.shortestLine(usable[c]),
                            "Ends %.2f m short of road %s" % (d, c), fid2=c, value=d,
                            extra={"part": pi, "end": which, "point": add.back(pg).asPoint()})
                        continue
                if edge_m is not None and edge_m.distance(pg) <= s.grid_edge_m:
                    continue
                add("r_dead_end", fid, pg, "Road end isn't connected to any other road")

    # ---- isolated network pieces
    if "r_isolated" in en and len(usable) > 1:
        tick(0.85, "Analysing network connectivity")
        parent = {fid: fid for fid in usable}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for fid, g in usable.items():
            for cid in idx.intersects(expand(g.boundingBox(), tol)):
                if cid <= fid:
                    continue
                if g.distance(usable[cid]) <= tol:
                    ra, rb = find(fid), find(cid)
                    if ra != rb:
                        parent[ra] = rb
        comps = {}
        for fid in usable:
            comps.setdefault(find(fid), []).append(fid)
        if len(comps) > 1:
            totals = {root: sum(lengths[f] for f in members) for root, members in comps.items()}
            main = max(totals, key=totals.get)
            for root, members in comps.items():
                if root == main or totals[root] > s.isolated_max_length_m:
                    continue
                mine = [f for f in members if f in in_scope]
                if not mine:
                    continue
                if edge_m is not None and any(edge_m.distance(usable[f]) <= s.grid_edge_m for f in members):
                    continue
                geom = QgsGeometry.collectGeometry([usable[f] for f in members])
                add("r_isolated", min(mine), geom,
                    "%d segment(s), %.0f m in total, not connected to the main network" % (len(members), totals[root]),
                    value=totals[root], extra={"members": sorted(members)})
    tick(1.0)
    return add.out
