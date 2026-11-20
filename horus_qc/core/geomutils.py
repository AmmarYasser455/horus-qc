"""Geometry helpers shared by the checks, fixes and review tools."""

import math

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
    QgsWkbTypes,
)

from ..compat import GEOM_LINE, GEOM_POINT, GEOM_POLYGON, METERS

WGS84 = QgsCoordinateReferenceSystem("EPSG:4326")


# ---------------------------------------------------------------- CRS helpers
def is_metric(crs):
    return crs.isValid() and not crs.isGeographic() and crs.mapUnits() == METERS


def utm_for_lonlat(lon, lat):
    if lat > 84:
        return QgsCoordinateReferenceSystem("EPSG:32661")
    if lat < -80:
        return QgsCoordinateReferenceSystem("EPSG:32761")
    zone = max(1, min(60, int((lon + 180) // 6) + 1))
    epsg = (32600 if lat >= 0 else 32700) + zone
    return QgsCoordinateReferenceSystem("EPSG:%d" % epsg)


def choose_metric_crs(crs, extent, context=None):
    """Return a CRS in metres for measuring around ``extent`` (given in ``crs``).

    Projected metre CRSs are used as-is; otherwise the matching UTM zone.
    """
    if is_metric(crs):
        return crs
    context = context or QgsProject.instance().transformContext()
    center = extent.center() if not extent.isNull() else QgsPointXY(0, 0)
    try:
        ct = QgsCoordinateTransform(crs, WGS84, context)
        ll = ct.transform(center)
    except Exception:
        ll = center
    return utm_for_lonlat(ll.x(), ll.y())


def transformer(src, dst, context=None):
    """Callable that returns a transformed *copy* of a geometry (identity if same CRS)."""
    if src == dst:
        return lambda g: QgsGeometry(g)
    ct = QgsCoordinateTransform(src, dst, context or QgsProject.instance().transformContext())

    def _tx(g):
        out = QgsGeometry(g)
        if not out.isNull():
            out.transform(ct)
        return out

    return _tx


def metres_to_units(metres, crs):
    """Rough conversion of a distance in metres to map units of ``crs``."""
    if crs.isGeographic():
        return metres / 111320.0
    return metres


def expand(rect, d):
    r = QgsRectangle(rect)
    r.grow(d)
    return r


# ---------------------------------------------------------------- parts
def polygon_parts(geom):
    """List of single-polygon QgsGeometry parts (non-polygon parts dropped)."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    out = []
    for part in geom.asGeometryCollection():
        if part.type() == GEOM_POLYGON:
            out.extend(part.asGeometryCollection() if part.isMultipart() else [part])
    return out


def line_parts(geom):
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    out = []
    for part in geom.asGeometryCollection():
        if part.type() == GEOM_LINE:
            out.extend(part.asGeometryCollection() if part.isMultipart() else [part])
    return out


def point_parts(geom):
    if geom is None or geom.isNull() or geom.isEmpty():
        return []
    return [p for p in geom.asGeometryCollection() if p.type() == GEOM_POINT]


def polygonal_area(geom):
    return sum(p.area() for p in polygon_parts(geom))


def polygonal(geom):
    """Collapse a geometry to its polygon parts (multipolygon or empty)."""
    parts = polygon_parts(geom)
    if not parts:
        return QgsGeometry()
    if len(parts) == 1:
        return parts[0]
    return QgsGeometry.collectGeometry(parts)


def part_count(geom):
    if geom is None or geom.isNull():
        return 0
    if not geom.isMultipart():
        return 1
    return geom.constGet().numGeometries()


def safe_point(geom):
    """A representative point that never throws on invalid geometry."""
    if geom is None or geom.isNull() or geom.isEmpty():
        return QgsGeometry()
    try:
        p = geom.pointOnSurface()
        if not p.isNull() and not p.isEmpty():
            return p
    except Exception:
        pass
    return QgsGeometry.fromPointXY(geom.boundingBox().center())


def match_layer_type(layer, geom):
    """Make ``geom`` match the layer's single/multi and Z/M flags."""
    g = QgsGeometry(geom)
    wkb = layer.wkbType()
    if QgsWkbTypes.isMultiType(wkb) and not g.isMultipart():
        g.convertToMultiType()
    elif not QgsWkbTypes.isMultiType(wkb) and g.isMultipart() and part_count(g) == 1:
        g.convertToSingleType()
    if QgsWkbTypes.hasZ(wkb) and not QgsWkbTypes.hasZ(g.wkbType()):
        g.get().addZValue(0)
    if QgsWkbTypes.hasM(wkb) and not QgsWkbTypes.hasM(g.wkbType()):
        g.get().addMValue(0)
    if not QgsWkbTypes.hasZ(wkb) and QgsWkbTypes.hasZ(g.wkbType()):
        g.get().dropZValue()
    if not QgsWkbTypes.hasM(wkb) and QgsWkbTypes.hasM(g.wkbType()):
        g.get().dropMValue()
    return g


# ---------------------------------------------------------------- rings & angles
def polygon_rings(geom):
    """[[ring, ...] per part] as lists of QgsPointXY (closing point kept)."""
    if geom.isMultipart():
        return [list(map(list, poly)) for poly in geom.asMultiPolygon()]
    return [list(map(list, geom.asPolygon()))]


def rings_to_geometry(parts):
    parts = [p for p in parts if p and len(p[0]) >= 4]
    if not parts:
        return QgsGeometry()
    if len(parts) == 1:
        return QgsGeometry.fromPolygonXY(parts[0])
    return QgsGeometry.fromMultiPolygonXY(parts)


def _signed_area(pts):
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i].x(), pts[i].y()
        x2, y2 = pts[(i + 1) % n].x(), pts[(i + 1) % n].y()
        a += x1 * y2 - x2 * y1
    return a / 2.0


def open_ring(ring):
    """Drop the closing point and consecutive duplicates."""
    pts = []
    for p in ring:
        if not pts or (abs(p.x() - pts[-1].x()) > 1e-9 or abs(p.y() - pts[-1].y()) > 1e-9):
            pts.append(QgsPointXY(p))
    if len(pts) > 1 and pts[0].compare(pts[-1], 1e-9):
        pts.pop()
    return pts


def interior_angles(pts):
    """Interior angle (degrees, 0..360) at each vertex of an open ring."""
    n = len(pts)
    if n < 3:
        return []
    orient = 1.0 if _signed_area(pts) >= 0 else -1.0
    out = []
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        h1 = math.atan2(b.y() - a.y(), b.x() - a.x())
        h2 = math.atan2(c.y() - b.y(), c.x() - b.x())
        turn = math.degrees(h2 - h1)
        while turn <= -180.0:
            turn += 360.0
        while turn > 180.0:
            turn -= 360.0
        out.append(180.0 - turn * orient)
    return out


def simplify_ring(pts, min_seg=0.05, straight_tol=5.0):
    """Remove tiny segments and almost-straight vertices (metric coordinates)."""
    pts = list(pts)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        n = len(pts)
        for i in range(n):
            a, b = pts[i - 1], pts[i]
            if a.distance(b) < min_seg:
                del pts[i]
                changed = True
                break
        if changed:
            continue
        angles = interior_angles(pts)
        for i, ang in enumerate(angles):
            if abs(ang - 180.0) < straight_tol:
                del pts[i]
                changed = True
                break
    return pts


def corner_report(geom, spike_deg, sq_min, sq_max):
    """Return (spikes, wobbly) lists of QgsPointXY for a metric polygon geometry."""
    spikes, wobbly = [], []
    for part in polygon_rings(geom):
        for ring in part[:1]:  # exterior ring only
            raw = simplify_ring(open_ring(ring), straight_tol=0.0)  # drop tiny segments only
            for p, ang in zip(raw, interior_angles(raw)):
                if ang < spike_deg or ang > 360.0 - spike_deg:
                    spikes.append(p)
            pts = simplify_ring(raw)
            if len(pts) < 4:
                continue
            for p, ang in zip(pts, interior_angles(pts)):
                if ang < spike_deg or ang > 360.0 - spike_deg:
                    continue
                dev = min(abs(ang - 90.0), abs(ang - 270.0))
                if sq_min < dev < sq_max:
                    wobbly.append(p)
    return spikes, wobbly


def remove_spikes(geom, spike_deg):
    """Return a copy of a metric polygon geometry with spike vertices removed."""
    out_parts = []
    for part in polygon_rings(geom):
        new_part = []
        for ring in part:
            pts = simplify_ring(open_ring(ring), straight_tol=0.0)
            changed = True
            while changed and len(pts) > 3:
                changed = False
                for i, ang in enumerate(interior_angles(pts)):
                    if ang < spike_deg or ang > 360.0 - spike_deg:
                        del pts[i]
                        changed = True
                        break
            if len(pts) >= 3:
                new_part.append(pts + [QgsPointXY(pts[0])])
        if new_part:
            out_parts.append(new_part)
    return rings_to_geometry(out_parts)


# ---------------------------------------------------------------- lines
def segment_intersection(p1, p2, p3, p4):
    """Intersection point of segments p1p2 and p3p4, or None."""
    d = (p2.x() - p1.x()) * (p4.y() - p3.y()) - (p2.y() - p1.y()) * (p4.x() - p3.x())
    if abs(d) < 1e-15:
        return None
    t = ((p3.x() - p1.x()) * (p4.y() - p3.y()) - (p3.y() - p1.y()) * (p4.x() - p3.x())) / d
    u = ((p3.x() - p1.x()) * (p2.y() - p1.y()) - (p3.y() - p1.y()) * (p2.x() - p1.x())) / d
    if 0.0 <= t <= 1.0 and 0.0 <= u <= 1.0:
        return QgsPointXY(p1.x() + t * (p2.x() - p1.x()), p1.y() + t * (p2.y() - p1.y()))
    return None


def self_crossing_point(line_geom, max_segments=4000):
    """First point where a (multi)line crosses itself, or None."""
    segs = []
    for pi, part in enumerate(line_parts(line_geom)):
        pts = part.asPolyline()
        for i in range(len(pts) - 1):
            segs.append((pi, i, pts[i], pts[i + 1]))
    if len(segs) > max_segments:
        return None
    for a in range(len(segs)):
        pa, ia, a1, a2 = segs[a]
        for b in range(a + 1, len(segs)):
            pb, ib, b1, b2 = segs[b]
            if pa == pb and abs(ia - ib) <= 1:
                continue
            ip = segment_intersection(a1, a2, b1, b2)
            if ip is not None:
                # ignore closed rings touching at their start/end
                if pa == pb and ip.compare(a1, 1e-9) and ip.compare(b2, 1e-9):
                    continue
                return ip
    return None


def endpoints(line_geom):
    """[(part_index, 'start'|'end', QgsPointXY)] for every open line part."""
    out = []
    for pi, part in enumerate(line_parts(line_geom)):
        pts = part.asPolyline()
        if len(pts) < 2:
            continue
        if pts[0].compare(pts[-1], 1e-9):
            continue  # closed loop: no ends
        out.append((pi, "start", pts[0]))
        out.append((pi, "end", pts[-1]))
    return out
