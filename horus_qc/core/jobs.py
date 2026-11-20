"""Build check jobs from live QGIS layers (main thread only)."""

from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsProject,
    QgsRectangle,
)

from .catalog import BUILDING_CHECKS, LIVE_CHECKS, ROAD_CHECKS
from .engine import Job, LayerData, run_job
from .geomutils import choose_metric_crs, expand, metres_to_units, safe_point, transformer

SCOPE_ALL = "all"
SCOPE_EXTENT = "extent"
SCOPE_SELECTED = "selected"
SCOPE_GRID_SELECTED = "grid_selected"
SCOPE_GRID_CENTRE = "grid_centre"

SCOPE_LABELS = [
    (SCOPE_GRID_SELECTED, "Selected grid cell(s)"),
    (SCOPE_GRID_CENTRE, "Grid cell at map centre"),
    (SCOPE_EXTENT, "Current map view"),
    (SCOPE_SELECTED, "Selected features"),
    (SCOPE_ALL, "Whole layer"),
]

NEIGHBOUR_MARGIN_M = 15.0


class ScopeError(ValueError):
    """Raised with a user-facing message when the scope can't be built."""


def _ctx():
    return QgsProject.instance().transformContext()


def _bbox(geoms):
    rect = QgsRectangle()
    rect.setMinimal()
    for g in geoms:
        rect.combineExtentWith(g.boundingBox())
    return rect


def grid_cells(grid_layer, scope, canvas=None):
    """Grid cell geometries (grid CRS) selected by the scope, or []."""
    if grid_layer is None:
        return []
    if scope == SCOPE_GRID_SELECTED:
        cells = [QgsGeometry(f.geometry()) for f in grid_layer.getSelectedFeatures()]
        if not cells:
            raise ScopeError("Select your grid cell(s) in the grid layer first.")
        return cells
    if scope == SCOPE_GRID_CENTRE:
        if canvas is None:
            raise ScopeError("No map canvas available.")
        centre = canvas.center()
        ct = QgsCoordinateTransform(canvas.mapSettings().destinationCrs(), grid_layer.crs(), _ctx())
        p = ct.transform(centre)
        pg = QgsGeometry.fromPointXY(p)
        req = QgsFeatureRequest().setFilterRect(QgsRectangle(p.x(), p.y(), p.x(), p.y()))
        for f in grid_layer.getFeatures(req):
            if f.geometry().intersects(pg):
                return [QgsGeometry(f.geometry())]
        raise ScopeError("The map centre isn't inside any grid cell.")
    return []


def _fetch(layer, rect_in_layer=None, fids=None):
    req = QgsFeatureRequest().setNoAttributes()
    if fids is not None:
        req.setFilterFids(list(fids))
    elif rect_in_layer is not None:
        req.setFilterRect(rect_in_layer)
    return {f.id(): QgsGeometry(f.geometry()) if f.hasGeometry() else None for f in layer.getFeatures(req)}


def _layer_data(layer, kind, area_geoms, area_crs, scope, focus=None):
    """Snapshot one layer: scope features plus a neighbour margin around them."""
    crs = layer.crs()
    margin = metres_to_units(NEIGHBOUR_MARGIN_M, crs)
    to_layer = transformer(area_crs, crs) if area_geoms else None

    if focus is not None:
        focus = set(focus)
        own = _fetch(layer, fids=focus)
        valid = [g for g in own.values() if g is not None and not g.isNull()]
        geoms = dict(own)
        if valid:
            geoms.update(_fetch(layer, expand(_bbox(valid), margin)))
        return LayerData(layer.id(), layer.name(), crs, geoms, focus & set(geoms))

    if scope == SCOPE_SELECTED:
        sel = set(layer.selectedFeatureIds())
        own = _fetch(layer, fids=sel) if sel else {}
        valid = [g for g in own.values() if g is not None and not g.isNull()]
        geoms = dict(own)
        if valid:
            geoms.update(_fetch(layer, expand(_bbox(valid), margin)))
        return LayerData(layer.id(), layer.name(), crs, geoms, sel)

    if not area_geoms:
        geoms = _fetch(layer)
        return LayerData(layer.id(), layer.name(), crs, geoms, set(geoms))

    areas = [to_layer(g) for g in area_geoms]
    geoms = _fetch(layer, expand(_bbox(areas), margin))
    scope_fids = set()
    for fid, g in geoms.items():
        if g is None or g.isNull():
            continue
        if kind == "building" and scope in (SCOPE_GRID_SELECTED, SCOPE_GRID_CENTRE):
            probe = safe_point(g)  # a building belongs to the grid holding its centre
        else:
            probe = g
        if any(a.intersects(probe) for a in areas):
            scope_fids.add(fid)
    return LayerData(layer.id(), layer.name(), crs, geoms, scope_fids)


def build_job(settings, buildings=None, roads=None, grid=None, scope=SCOPE_ALL, canvas=None,
              enabled=None, ignored=None, focus=None, road_focus=None):
    """Snapshot the layers for ``run_job``. Raises ScopeError with a readable message."""
    if buildings is None and roads is None:
        raise ScopeError("Choose a buildings layer and/or a roads layer.")
    if scope in (SCOPE_GRID_SELECTED, SCOPE_GRID_CENTRE) and grid is None and focus is None and road_focus is None:
        raise ScopeError("This scope needs a grid layer.")

    enabled = set(settings.enabled_checks if enabled is None else enabled)
    if buildings is None:
        enabled -= {c.key for c in BUILDING_CHECKS}
    if roads is None:
        enabled -= {c.key for c in ROAD_CHECKS}

    base = buildings or roads
    area_geoms, area_crs, edges = [], base.crs(), []
    live = focus is not None or road_focus is not None
    if not live:
        if scope == SCOPE_EXTENT:
            if canvas is None:
                raise ScopeError("No map canvas available.")
            area_geoms = [QgsGeometry.fromRect(canvas.extent())]
            area_crs = canvas.mapSettings().destinationCrs()
            edges = list(area_geoms)
        elif scope in (SCOPE_GRID_SELECTED, SCOPE_GRID_CENTRE):
            area_geoms = grid_cells(grid, scope, canvas)
            area_crs = grid.crs()
            edges = list(area_geoms)

    b_data = r_data = None
    if buildings is not None and (not live or focus):
        b_data = _layer_data(buildings, "building", area_geoms, area_crs, scope, focus if live else None)
    if roads is not None:
        if live:
            if road_focus:
                r_data = _layer_data(roads, "road", [], area_crs, scope, road_focus)
            elif b_data is not None:
                # live building edit: roads around the edited buildings, no road checks reported
                valid = [g for f, g in b_data.geoms.items() if g is not None and not g.isNull()]
                rect = transformer(buildings.crs(), roads.crs())(QgsGeometry.fromRect(_bbox(valid))).boundingBox()
                geoms = _fetch(roads, expand(rect, metres_to_units(NEIGHBOUR_MARGIN_M, roads.crs())))
                r_data = LayerData(roads.id(), roads.name(), roads.crs(), geoms, set())
        else:
            r_data = _layer_data(roads, "road", area_geoms, area_crs, scope)

    # grid cells used by the building-vs-grid checks
    grid_geoms, grid_crs = [], None
    if grid is not None:
        grid_crs = grid.crs()
        if scope in (SCOPE_GRID_SELECTED, SCOPE_GRID_CENTRE) and area_geoms and not live:
            grid_geoms = list(area_geoms)
        else:
            ref = b_data or r_data
            valid = [g for g in ref.geoms.values() if g is not None and not g.isNull()]
            if valid:
                rect = transformer(ref.crs, grid_crs)(QgsGeometry.fromRect(_bbox(valid))).boundingBox()
                grid_geoms = [g for g in _fetch(grid, expand(rect, metres_to_units(50, grid_crs))).values()
                              if g is not None and not g.isNull()]
    if edges and grid_crs is None:
        grid_crs = area_crs
    elif edges and area_crs != grid_crs:
        tx = transformer(area_crs, grid_crs)
        edges = [tx(g) for g in edges]

    ref = b_data or r_data
    extent = _bbox([g for g in ref.geoms.values() if g is not None and not g.isNull()]) if ref.geoms else QgsRectangle()
    metric = choose_metric_crs(ref.crs, extent, _ctx())

    return Job(
        settings=settings,
        enabled=enabled,
        context=_ctx(),
        metric_crs=metric,
        buildings=b_data,
        roads=r_data if (r_data is not None) else None,
        grid_crs=grid_crs,
        grid_geoms=grid_geoms,
        edge_geoms=edges,
        ignored=set(ignored or ()),
    )


def live_check(settings, buildings=None, roads=None, grid=None, building_fids=None, road_fids=None, ignored=None):
    """Synchronously check just-edited features with the fast 'live' subset of checks."""
    enabled = set(settings.enabled_checks) & LIVE_CHECKS
    if not building_fids:
        enabled -= {c.key for c in BUILDING_CHECKS}
    if not road_fids:
        enabled -= {c.key for c in ROAD_CHECKS}
    if not enabled:
        return []
    job = build_job(settings, buildings if building_fids else None, roads, grid, SCOPE_ALL, None,
                    enabled=enabled, ignored=ignored, focus=building_fids or None,
                    road_focus=road_fids or None)
    if not building_fids:
        job.buildings = None
    return [e for e in run_job(job) if e.status == "open"]
