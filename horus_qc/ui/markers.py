"""Map display: a styled error-marker layer, highlight rubber bands and zoom helpers."""

from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QColor
from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsCoordinateTransform,
    QgsFeature,
    QgsGeometry,
    QgsMarkerSymbol,
    QgsProject,
    QgsRectangle,
    QgsRendererCategory,
    QgsVectorLayer,
)
from qgis.gui import QgsRubberBand

from ..compat import GEOM_LINE, GEOM_POINT, GEOM_POLYGON, rubber_icon
from ..core.catalog import CHECKS, SEVERITY_COLORS
from ..core.geomutils import metres_to_units, safe_point

GROUP_NAME = "Horus QC"
LAYER_NAME = "Horus QC errors"


def _tx(src, dst):
    return QgsCoordinateTransform(src, dst, QgsProject.instance())


class ErrorLayer:
    """A temporary point layer with one marker per open error, coloured by severity."""

    def __init__(self):
        self.layer_id = None
        self._fids = {}  # error index -> feature id

    def layer(self):
        return QgsProject.instance().mapLayer(self.layer_id) if self.layer_id else None

    def clear(self):
        lyr = self.layer()
        if lyr is not None:
            QgsProject.instance().removeMapLayer(lyr.id())
        self.layer_id = None
        self._fids = {}
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(GROUP_NAME)
        if group is not None and not group.children():
            root.removeChildNode(group)

    def rebuild(self, errors, crs):
        self.clear()
        lyr = QgsVectorLayer(
            "Point?field=eid:integer&field=severity:string(10)&field=check:string(30)"
            "&field=title:string(60)&field=layer:string(80)&field=feature_id:integer&field=message:string(250)",
            LAYER_NAME, "memory")
        lyr.setCrs(crs)
        feats, order = [], []
        cache = {}
        for i, err in enumerate(errors):
            if err.status != "open" or err.geometry is None or err.geometry.isNull():
                continue
            src = QgsProject.instance().mapLayer(err.layer_id)
            src_crs = src.crs() if src is not None else crs
            key = src_crs.authid() or src_crs.toWkt()
            if key not in cache:
                cache[key] = _tx(src_crs, crs)
            p = safe_point(err.geometry)
            if p.isNull():
                continue
            try:
                p.transform(cache[key])
            except Exception:
                continue
            f = QgsFeature(lyr.fields())
            f.setGeometry(p)
            f.setAttributes([i, err.severity, err.check, CHECKS[err.check].title, err.layer_name, int(err.fid), err.message])
            feats.append(f)
            order.append(i)
        ok, added = lyr.dataProvider().addFeatures(feats)
        if ok:
            self._fids = {i: f.id() for i, f in zip(order, added)}
        cats = []
        for sev, color in SEVERITY_COLORS.items():
            sym = QgsMarkerSymbol.createSimple({
                "name": "circle", "color": color, "outline_color": "#ffffff",
                "outline_width": "0.4", "size": "3.2",
            })
            cats.append(QgsRendererCategory(sev, sym, sev.capitalize()))
        lyr.setRenderer(QgsCategorizedSymbolRenderer("severity", cats))
        project = QgsProject.instance()
        project.addMapLayer(lyr, False)
        root = project.layerTreeRoot()
        group = root.findGroup(GROUP_NAME) or root.insertGroup(0, GROUP_NAME)
        group.insertLayer(0, lyr)
        self.layer_id = lyr.id()
        return lyr

    def remove(self, index):
        lyr = self.layer()
        fid = self._fids.pop(index, None)
        if lyr is not None and fid is not None:
            lyr.dataProvider().deleteFeatures([fid])
            lyr.triggerRepaint()


class Highlighter:
    """Rubber bands for the selected error, the review building and flashes."""

    def __init__(self, canvas):
        self.canvas = canvas
        self.error_bands = []
        self.current_bands = []
        self.pick_band = None

    # ------------------------------------------------------------- primitives
    def _band(self, geom, crs, color, width=3, fill_alpha=0, dashed=False, icon_size=18):
        if geom is None or geom.isNull() or geom.isEmpty():
            return None
        gtype = geom.type()
        if gtype not in (GEOM_POINT, GEOM_LINE, GEOM_POLYGON):
            return None
        rb = QgsRubberBand(self.canvas, gtype)
        c = QColor(color)
        rb.setStrokeColor(c)
        fill = QColor(c)
        fill.setAlpha(fill_alpha)
        rb.setFillColor(fill)
        rb.setWidth(width)
        if gtype == GEOM_POINT:
            rb.setIcon(rubber_icon("circle"))
            rb.setIconSize(icon_size)
        if dashed:
            rb.setLineStyle(Qt.PenStyle.DashLine)
        rb.setToGeometry(QgsGeometry(geom), crs)
        rb.show()
        return rb

    def _drop(self, bands):
        for rb in bands:
            try:
                rb.reset()
                self.canvas.scene().removeItem(rb)
            except RuntimeError:
                pass
        bands.clear()

    def clear(self):
        self._drop(self.error_bands)
        self._drop(self.current_bands)
        self.clear_pick()

    # ------------------------------------------------------------- errors
    def show_error(self, err):
        self._drop(self.error_bands)
        project = QgsProject.instance()
        layer = project.mapLayer(err.layer_id)
        if layer is not None:
            f = layer.getFeature(err.fid)
            if f.isValid() and f.hasGeometry():
                self.error_bands.append(self._band(f.geometry(), layer.crs(), "#ff8c00", 3))
            if err.fid2 is not None:
                other = project.mapLayer(err.other_layer_id) if err.other_layer_id else layer
                if other is not None:
                    f2 = other.getFeature(err.fid2)
                    if f2.isValid() and f2.hasGeometry():
                        self.error_bands.append(self._band(f2.geometry(), other.crs(), "#d633ff", 2, dashed=True))
            if err.geometry is not None and not err.geometry.isNull():
                self.error_bands.append(self._band(err.geometry, layer.crs(), "#ffe600", 4, fill_alpha=110))
        self.error_bands = [b for b in self.error_bands if b is not None]

    # ------------------------------------------------------------- review
    def show_current(self, layer, fid):
        self._drop(self.current_bands)
        if layer is None or fid is None:
            return
        f = layer.getFeature(fid)
        if f.isValid() and f.hasGeometry():
            rb = self._band(f.geometry(), layer.crs(), "#00e5ff", 3, fill_alpha=35)
            if rb is not None:
                self.current_bands.append(rb)

    def show_pick(self, start, end, crs):
        self.clear_pick()
        line = QgsGeometry.fromPolylineXY([start, end])
        self.pick_band = self._band(line, crs, "#00ff6a", 3)

    def clear_pick(self):
        if self.pick_band is not None:
            self._drop([self.pick_band])
            self.pick_band = None

    def flash(self, items, ms=4000):
        """Briefly draw (geometry, crs) pairs in red (used by the live check)."""
        bands = [self._band(g, crs, "#ff1744", 4, fill_alpha=90) for g, crs in items]
        bands = [b for b in bands if b is not None]
        if bands:
            QTimer.singleShot(ms, lambda: self._drop(bands))


# ------------------------------------------------------------------ zooming
def zoom_to(canvas, geom, crs, min_size_m=25.0):
    if geom is None or geom.isNull():
        return
    dest = canvas.mapSettings().destinationCrs()
    try:
        rect = _tx(crs, dest).transformBoundingBox(geom.boundingBox())
    except Exception:
        return
    m = metres_to_units(min_size_m, dest)
    if rect.width() < m and rect.height() < m:
        c = rect.center()
        rect = QgsRectangle(c.x() - m / 2, c.y() - m / 2, c.x() + m / 2, c.y() + m / 2)
    rect.scale(1.5)
    canvas.setExtent(rect)
    canvas.refresh()


def centre_on(canvas, geom, crs, scale=None):
    """Centre the map on a geometry at a fixed scale, zooming out if it doesn't fit."""
    if geom is None or geom.isNull():
        return
    dest = canvas.mapSettings().destinationCrs()
    try:
        rect = _tx(crs, dest).transformBoundingBox(geom.boundingBox())
    except Exception:
        return
    if scale:
        canvas.setCenter(rect.center())
        canvas.zoomScale(scale)
        ext = canvas.extent()
        if rect.width() > ext.width() * 0.8 or rect.height() > ext.height() * 0.8:
            zoom_to(canvas, geom, crs)
            return
    else:
        canvas.setCenter(rect.center())
    canvas.refresh()
