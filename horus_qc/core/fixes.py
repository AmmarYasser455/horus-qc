"""One-click fixes. Every fix is a single undoable edit command (Ctrl+Z)."""

from qgis.core import QgsGeometry, QgsPointXY, QgsProject, QgsVectorLayerUtils

from ..compat import GEOM_LINE, GEOM_POLYGON
from .geomutils import (
    choose_metric_crs,
    line_parts,
    match_layer_type,
    polygon_parts,
    polygon_rings,
    remove_spikes,
    rings_to_geometry,
    transformer,
)


class FixError(Exception):
    pass


def _layer(layer_id):
    lyr = QgsProject.instance().mapLayer(layer_id)
    if lyr is None:
        raise FixError("The layer is no longer in the project.")
    return lyr


def _feature(layer, fid):
    f = layer.getFeature(fid)
    if not f.isValid():
        raise FixError("Feature %s no longer exists." % fid)
    return f


def ensure_editable(layer):
    if layer.isEditable():
        return
    if not layer.startEditing():
        raise FixError("Layer '%s' can't be edited (read-only?)." % layer.name())


def _parts_for_layer(layer, geom):
    if layer.geometryType() == GEOM_POLYGON:
        parts = polygon_parts(geom)
        parts.sort(key=lambda p: p.area(), reverse=True)
    elif layer.geometryType() == GEOM_LINE:
        parts = line_parts(geom)
        parts.sort(key=lambda p: p.length(), reverse=True)
    else:
        parts = [geom]
    return [p for p in parts if not p.isNull() and not p.isEmpty()]


def _write(layer, feature, geom, keep_together):
    """Write ``geom`` to ``feature``; extra parts become new features if needed."""
    parts = _parts_for_layer(layer, geom)
    if not parts:
        raise FixError("The fix produced an empty geometry; nothing was changed.")
    from qgis.core import QgsWkbTypes

    multi_layer = QgsWkbTypes.isMultiType(layer.wkbType())
    if keep_together and multi_layer:
        layer.changeGeometry(feature.id(), match_layer_type(layer, QgsGeometry.collectGeometry(parts)))
        return 0
    layer.changeGeometry(feature.id(), match_layer_type(layer, parts[0]))
    pk = set(layer.primaryKeyAttributes())
    attrs = {i: feature.attribute(i) for i in range(len(layer.fields())) if i not in pk}
    for part in parts[1:]:
        nf = QgsVectorLayerUtils.createFeature(layer, match_layer_type(layer, part), attrs)
        layer.addFeature(nf)
    return len(parts) - 1


def _metric(layer, geom):
    m = choose_metric_crs(layer.crs(), geom.boundingBox())
    return transformer(layer.crs(), m), transformer(m, layer.crs())


# --------------------------------------------------------------------------- fixes
def fix_delete(layer, err, settings):
    _feature(layer, err.fid)
    layer.deleteFeature(err.fid)
    return "Deleted feature %s" % err.fid


def fix_make_valid(layer, err, settings):
    f = _feature(layer, err.fid)
    g = f.geometry().makeValid()
    extra = _write(layer, f, g, keep_together=True)
    return "Repaired feature %s" % err.fid + (" (+%d new part features)" % extra if extra else "")


def fix_untangle(layer, err, settings):
    """A 4-corner bow-tie becomes the quadrilateral through the same corners (convex hull);
    anything more complex falls back to makeValid."""
    f = _feature(layer, err.fid)
    g = f.geometry()
    rings = polygon_rings(g)
    if len(rings) == 1 and len(rings[0]) == 1:
        from .geomutils import open_ring

        corners = open_ring(rings[0][0])
        if len(corners) == 4:
            hull = QgsGeometry.fromMultiPointXY(corners).convexHull()
            if hull.type() == GEOM_POLYGON and len(open_ring(hull.asPolygon()[0])) == 4:
                _write(layer, f, hull, keep_together=True)
                return "Untangled bow-tie building %s" % err.fid
    return fix_make_valid(layer, err, settings)


def fix_split_parts(layer, err, settings):
    f = _feature(layer, err.fid)
    extra = _write(layer, f, f.geometry(), keep_together=False)
    return "Split feature %s into %d features" % (err.fid, extra + 1)


def fix_trim(layer, err, settings):
    f = _feature(layer, err.fid)
    other = _feature(layer, err.fid2).geometry()
    g = f.geometry()
    if not g.isGeosValid():
        g = g.makeValid()
    if not other.isGeosValid():
        other = other.makeValid()
    res = g.difference(other)
    parts = [p for p in polygon_parts(res) if p.area() > 0]
    if not parts:
        raise FixError("Cutting the overlap would remove the whole building. Delete it instead.")
    total = sum(p.area() for p in parts)
    keep = [p for p in parts if p.area() >= max(0.02 * total, 1e-12)]
    _write(layer, f, QgsGeometry.collectGeometry(keep), keep_together=True)
    return "Cut overlap with %s out of building %s" % (err.fid2, err.fid)


def fix_snap_gap(layer, err, settings):
    f = _feature(layer, err.fid)
    other = _feature(layer, err.fid2).geometry()
    to_m, back = _metric(layer, f.geometry())
    gm, om = to_m(f.geometry()), to_m(other)
    boundary = QgsGeometry(om.constGet().boundary())
    tol = settings.gap_tolerance_m * 1.05 + 1e-6
    moved = 0
    parts = []
    for part in polygon_rings(gm):
        new_part = []
        for ring in part:
            new_ring = []
            for p in ring:
                pg = QgsGeometry.fromPointXY(p)
                if boundary.distance(pg) <= tol:
                    q = boundary.nearestPoint(pg).asPoint()
                    new_ring.append(QgsPointXY(q))
                    moved += 1
                else:
                    new_ring.append(QgsPointXY(p))
            new_part.append(new_ring)
        parts.append(new_part)
    if not moved:
        raise FixError("No vertex is close enough to the neighbour to snap.")
    res = rings_to_geometry(parts)
    if not res.isGeosValid():
        res = res.makeValid()
    if om.isGeosValid():
        cut = res.difference(om)  # remove any tiny overlap created by snapping
        if not cut.isNull() and not cut.isEmpty():
            res = cut
    _write(layer, f, back(res), keep_together=True)
    return "Snapped %d vertex(es) of building %s onto building %s" % (moved, err.fid, err.fid2)


def fix_orthogonalize(layer, err, settings):
    f = _feature(layer, err.fid)
    to_m, back = _metric(layer, f.geometry())
    res = to_m(f.geometry()).orthogonalize(1e-8, 1000, settings.square_max_dev_deg)
    if res.isNull() or res.isEmpty():
        raise FixError("QGIS couldn't square this outline.")
    _write(layer, f, back(res), keep_together=True)
    return "Squared the corners of building %s" % err.fid


def fix_remove_spikes(layer, err, settings):
    f = _feature(layer, err.fid)
    to_m, back = _metric(layer, f.geometry())
    res = remove_spikes(to_m(f.geometry()), settings.spike_angle_deg)
    if res.isNull() or res.isEmpty():
        raise FixError("Removing the spikes would leave no polygon.")
    _write(layer, f, back(res), keep_together=True)
    return "Removed spike vertices from building %s" % err.fid


def fix_snap_undershoot(layer, err, settings):
    f = _feature(layer, err.fid)
    target = _feature(layer, err.fid2)
    p = err.extra.get("point")
    if p is None:
        raise FixError("End point unknown; run the checks again.")
    g = QgsGeometry(f.geometry())
    t = QgsGeometry(target.geometry())
    q = t.nearestPoint(QgsGeometry.fromPointXY(QgsPointXY(p))).asPoint()
    _pt, vidx, _b, _a, _d = g.closestVertex(QgsPointXY(p))
    if vidx < 0 or not g.moveVertex(q.x(), q.y(), vidx):
        raise FixError("Couldn't move the road end.")
    layer.changeGeometry(f.id(), g)
    # add a junction vertex on the target road so the network shares a node
    _sq, at, after, _left = t.closestSegmentWithContext(QgsPointXY(q))
    _vpt, _vi, _b2, _a2, vdist = t.closestVertex(QgsPointXY(q))
    if after > 0 and vdist > 1e-12 and t.insertVertex(q.x(), q.y(), after):
        layer.changeGeometry(target.id(), t)
    return "Snapped road %s onto road %s" % (err.fid, err.fid2)


def fix_trim_overshoot(layer, err, settings):
    f = _feature(layer, err.fid)
    g = f.geometry()
    parts = line_parts(g)
    if len(parts) != 1:
        raise FixError("Trimming multipart roads isn't supported; split it first.")
    cross = err.extra.get("cross")
    if cross is None:
        raise FixError("Crossing point unknown; run the checks again.")
    to_m, back = _metric(layer, g)
    line = to_m(parts[0])
    along = line.lineLocatePoint(to_m(QgsGeometry.fromPointXY(QgsPointXY(cross))))
    if along < 0:
        raise FixError("Couldn't locate the junction on the road.")
    a, b = (along, line.length()) if err.extra.get("end") == "start" else (0.0, along)
    sub = QgsGeometry(line.constGet().curveSubstring(a, b))
    if sub.isNull() or sub.length() <= 0:
        raise FixError("Trimming would remove the whole road.")
    _write(layer, f, back(sub), keep_together=True)
    return "Trimmed the overshoot of road %s" % err.fid


FIXERS = {
    "delete": fix_delete,
    "make_valid": fix_make_valid,
    "untangle": fix_untangle,
    "split_parts": fix_split_parts,
    "trim": fix_trim,
    "snap_gap": fix_snap_gap,
    "orthogonalize": fix_orthogonalize,
    "remove_spikes": fix_remove_spikes,
    "snap_undershoot": fix_snap_undershoot,
    "trim_overshoot": fix_trim_overshoot,
}


def apply_fix(err, settings):
    """Apply the error's auto-fix as one undoable edit command. Returns a message."""
    fix_id = err.definition.autofix
    if not fix_id:
        raise FixError("This error has no automatic fix; follow the steps in the guidance panel.")
    if err.status != "open":
        raise FixError("This error is already %s." % err.status)
    layer = _layer(err.layer_id)
    ensure_editable(layer)
    layer.beginEditCommand("Horus QC: %s" % err.definition.autofix_label)
    try:
        msg = FIXERS[fix_id](layer, err, settings)
    except Exception:
        layer.destroyEditCommand()
        raise
    layer.endEditCommand()
    layer.triggerRepaint()
    err.status = "fixed"
    return msg
