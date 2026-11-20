"""Review mode: a keyboard-driven map tool for the daily building review.

Keys (with the map focused):
  E approve + next   F flag + next   N skip   Q back
  1-9 shift to base by N floors   G repeat last shift
  W A S D nudge (Shift = big step)   V pick roof->base vector
  ] / [ next / previous QC error   C check this building now   Esc cancel
"""

import json
import math

from qgis.PyQt.QtCore import QObject, Qt, pyqtSignal
from qgis.core import (
    QgsCoordinateTransform,
    QgsFeatureRequest,
    QgsGeometry,
    QgsPointXY,
    QgsProject,
    QgsRectangle,
)
from qgis.gui import QgsMapTool

from ..core.geomutils import choose_metric_crs, safe_point, transformer
from ..core.editing import EditError, ensure_editable
from ..core.review import APPROVED, FLAGGED, DailyStats, has_review_fields, serpentine_order, set_status, status_of
from .markers import centre_on

PROJECT_SCOPE = "horus_qc"
VECTORS_KEY = "shift_vectors"


class ReviewController(QObject):
    changed = pyqtSignal()  # queue position, stats or vector changed
    message = pyqtSignal(str, int)  # text, level (0 info, 1 warning, 3 success)
    error_nav = pyqtSignal(int)  # +1 / -1 : next / previous QC error
    check_now = pyqtSignal(object)  # building fid to live-check
    tool_toggled = pyqtSignal(bool)

    def __init__(self, iface, highlighter, settings_getter):
        super().__init__()
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.hl = highlighter
        self.settings = settings_getter
        self.layer = None
        self.grid = None
        self.queue = []
        self.pos = -1
        self.current = None
        self.session_status = {}
        self.stats = DailyStats()
        self.vectors = {}
        self.last_mult = 1
        self.ref_floors = 1
        self.only_unreviewed = True
        self._pick = 0
        self._roof = None
        self.tool = ReviewTool(self.canvas, self)
        self.load_vectors()

    # ------------------------------------------------------------- setup
    def set_layers(self, buildings, grid):
        if buildings is not self.layer:
            self.queue, self.pos, self.current = [], -1, None
            self.session_status = {}
            self.hl.show_current(None, None)
            if self.layer is not None:
                try:
                    self.layer.geometryChanged.disconnect(self._on_geometry_changed)
                except (TypeError, RuntimeError):
                    pass
            if buildings is not None:
                # keep the highlight on the building after undo/redo or edits by other tools
                buildings.geometryChanged.connect(self._on_geometry_changed)
        self.layer = buildings
        self.grid = grid
        self.changed.emit()

    def _on_geometry_changed(self, fid, _geom):
        if fid == self.current:
            self.refresh_highlight()

    def set_queue(self, fids):
        s = self.settings()
        self.queue = serpentine_order(self.layer, fids, s.serpentine_band_m) if self.layer else []
        self.pos = -1
        self.current = None
        self.changed.emit()
        return len(self.queue)

    def activate(self):
        self.canvas.setMapTool(self.tool)
        self.canvas.setFocus()
        if self.current is None and self.queue:
            self.go(+1)

    def deactivate(self):
        if self.canvas.mapTool() is self.tool:
            self.canvas.unsetMapTool(self.tool)

    def on_tool_deactivated(self):
        self._pick = 0
        self.hl.clear_pick()
        self.tool_toggled.emit(False)

    # ------------------------------------------------------------- status
    def status(self, fid):
        if self.layer is not None and has_review_fields(self.layer):
            return status_of(self.layer, fid)
        return self.session_status.get(fid, "")

    def remaining(self):
        return sum(1 for f in self.queue if self.status(f) not in (APPROVED, FLAGGED))

    # ------------------------------------------------------------- navigation
    def go(self, step):
        if not self.queue:
            self.message.emit("Press Start review on the Review tab first.", 1)
            return
        i = self.pos
        for _ in range(len(self.queue)):
            i += step
            if i < 0 or i >= len(self.queue):
                break
            if step < 0 or not self.only_unreviewed or self.status(self.queue[i]) not in (APPROVED, FLAGGED):
                self._set_current(self.queue[i], i)
                return
        self.message.emit("No more buildings in this direction." if step < 0 else
                          "All buildings in the queue are reviewed. Well done!", 3 if step > 0 else 0)

    def _set_current(self, fid, pos=None):
        self.current = fid
        if pos is not None:
            self.pos = pos
        elif fid in self.queue:
            self.pos = self.queue.index(fid)
        self.hl.show_current(self.layer, fid)
        s = self.settings()
        f = self.layer.getFeature(fid) if self.layer else None
        if f is not None and f.isValid() and f.hasGeometry() and s.auto_zoom:
            centre_on(self.canvas, f.geometry(), self.layer.crs(), s.zoom_scale)
        self.changed.emit()

    def refresh_highlight(self):
        self.hl.show_current(self.layer, self.current)

    def select_at(self, map_point):
        if self.layer is None:
            return
        ct = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), self.layer.crs(), QgsProject.instance())
        try:
            p = ct.transform(map_point)
        except Exception:
            return
        tol = self.canvas.mapUnitsPerPixel() * 6
        tol = ct.transformBoundingBox(QgsRectangle(map_point.x() - tol, map_point.y() - tol,
                                                   map_point.x() + tol, map_point.y() + tol))
        pg = QgsGeometry.fromPointXY(p)
        best = None
        req = QgsFeatureRequest().setFilterRect(tol).setNoAttributes()
        for f in self.layer.getFeatures(req):
            g = f.geometry()
            inside = g.contains(pg)
            d = 0.0 if inside else g.distance(pg)
            rank = (0 if inside else 1, g.area() if inside else d)
            if best is None or rank < best[0]:
                best = (rank, f.id())
        if best is not None:
            self._set_current(best[1])

    # ------------------------------------------------------------- review actions
    def mark(self, status):
        if self.current is None:
            self.message.emit("No building selected. Press N or click a building.", 1)
            return
        s = self.settings()
        if self.layer is not None and has_review_fields(self.layer):
            if not set_status(self.layer, [self.current], status, s.reviewer):
                self.message.emit("Couldn't write the review status (is the layer read-only?).", 1)
                return
        else:
            self.session_status[self.current] = status
        self.stats.bump("approved" if status == APPROVED else "flagged")
        self.go(+1)

    def approve(self):
        self.mark(APPROVED)

    def flag(self):
        self.mark(FLAGGED)

    # ------------------------------------------------------------- shifting
    def _targets(self):
        if self.layer is None:
            return []
        sel = list(self.layer.selectedFeatureIds())
        if sel and (self.current is None or self.current in sel):
            return sel
        return [self.current] if self.current is not None else []

    def grid_key(self, geom_layer_crs=None):
        if self.grid is None or geom_layer_crs is None:
            return "default"
        p = safe_point(geom_layer_crs)
        tx = transformer(self.layer.crs(), self.grid.crs())
        p = tx(p)
        if p.isNull():
            return "default"
        pt = p.asPoint()
        req = QgsFeatureRequest().setFilterRect(QgsRectangle(pt.x(), pt.y(), pt.x(), pt.y())).setNoAttributes()
        for f in self.grid.getFeatures(req):
            if f.geometry().intersects(p):
                return "%s:%s" % (self.grid.id(), f.id())
        return "default"

    def vector_for(self, fid):
        geom = None
        if fid is not None and self.layer is not None:
            f = self.layer.getFeature(fid)
            geom = f.geometry() if f.isValid() else None
        key = self.grid_key(geom)
        return self.vectors.get(key) or self.vectors.get("default"), key

    def translate(self, fids, dx_m, dy_m, label):
        if not fids:
            self.message.emit("No building selected. Press N or click a building.", 1)
            return 0
        try:
            ensure_editable(self.layer)
        except EditError as e:
            self.message.emit(str(e), 1)
            return 0
        self.layer.beginEditCommand("Horus QC: %s" % label)
        moved = 0
        for fid in fids:
            f = self.layer.getFeature(fid)
            if not f.isValid() or not f.hasGeometry():
                continue
            g = f.geometry()
            metric = choose_metric_crs(self.layer.crs(), g.boundingBox())
            gm = transformer(self.layer.crs(), metric)(g)
            gm.translate(dx_m, dy_m)
            self.layer.changeGeometry(fid, transformer(metric, self.layer.crs())(gm))
            moved += 1
        self.layer.endEditCommand()
        self.layer.triggerRepaint()
        self.refresh_highlight()
        return moved

    def shift(self, mult=None):
        mult = self.last_mult if mult is None else mult
        vec, _key = self.vector_for(self.current)
        if not vec:
            self.message.emit("No lean yet: press V, click a roof corner, then the same corner at the base.", 1)
            return
        n = self.translate(self._targets(), vec[0] * mult, vec[1] * mult, "shift to base x%d" % mult)
        if n:
            self.last_mult = mult
            self.stats.bump("shifted", n)
            self.changed.emit()

    def nudge(self, dx, dy, big=False):
        s = self.settings()
        step = s.nudge_big_m if big else s.nudge_m
        self.translate(self._targets(), dx * step, dy * step, "nudge")

    # ------------------------------------------------------------- vector picking
    def start_pick(self):
        self._pick = 1
        self._roof = None
        if self.canvas.mapTool() is not self.tool:
            self.activate()
        self.message.emit("Click a ROOF corner of a building…", 0)

    def on_click(self, map_point):
        if self._pick == 1:
            self._roof = QgsPointXY(map_point)
            self._pick = 2
            self.message.emit("…now click the SAME corner at the BASE of the building.", 0)
            return
        if self._pick == 2:
            self._finish_pick(QgsPointXY(map_point))
            return
        self.select_at(map_point)

    def on_move(self, map_point):
        if self._pick == 2 and self._roof is not None:
            self.hl.show_pick(self._roof, QgsPointXY(map_point), self.canvas.mapSettings().destinationCrs())

    def _finish_pick(self, base):
        self._pick = 0
        self.hl.clear_pick()
        crs = self.canvas.mapSettings().destinationCrs()
        rect = QgsRectangle(self._roof, base)
        metric = choose_metric_crs(crs, rect)
        tx = transformer(crs, metric)
        a = tx(QgsGeometry.fromPointXY(self._roof)).asPoint()
        b = tx(QgsGeometry.fromPointXY(base)).asPoint()
        floors = max(1, int(self.ref_floors))
        dx, dy = (b.x() - a.x()) / floors, (b.y() - a.y()) / floors
        base_layer = None
        if self.layer is not None:
            base_layer = transformer(crs, self.layer.crs())(QgsGeometry.fromPointXY(base))
        key = self.grid_key(base_layer)
        self.vectors[key] = [dx, dy]
        self.vectors["default"] = [dx, dy]
        self.save_vectors()
        self.message.emit("Lean saved: %s per floor. Now press 1-9 to move buildings to their base." % describe_vector([dx, dy]), 3)
        self.changed.emit()

    def cancel_pick(self):
        if self._pick:
            self._pick = 0
            self.hl.clear_pick()
            self.message.emit("Vector picking cancelled.", 0)
            return True
        return False

    def clear_vectors(self):
        self.vectors = {}
        self.save_vectors()
        self.changed.emit()

    def load_vectors(self):
        raw, ok = QgsProject.instance().readEntry(PROJECT_SCOPE, VECTORS_KEY, "")
        try:
            self.vectors = json.loads(raw) if raw else {}
        except ValueError:
            self.vectors = {}

    def save_vectors(self):
        QgsProject.instance().writeEntry(PROJECT_SCOPE, VECTORS_KEY, json.dumps(self.vectors))

    # ------------------------------------------------------------- keyboard
    def on_key(self, key, modifiers):
        if modifiers & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
            return False
        big = bool(modifiers & Qt.KeyboardModifier.ShiftModifier)
        K = Qt.Key
        digits = {getattr(K, "Key_%d" % d): d for d in range(1, 10)}
        if key in digits:
            self.shift(digits[key])
            return True
        actions = {
            K.Key_E: self.approve,
            K.Key_F: self.flag,
            K.Key_N: lambda: self.go(+1),
            K.Key_Q: lambda: self.go(-1),
            K.Key_G: lambda: self.shift(None),
            K.Key_W: lambda: self.nudge(0, 1, big),
            K.Key_S: lambda: self.nudge(0, -1, big),
            K.Key_A: lambda: self.nudge(-1, 0, big),
            K.Key_D: lambda: self.nudge(1, 0, big),
            K.Key_V: self.start_pick,
            K.Key_BracketRight: lambda: self.error_nav.emit(1),
            K.Key_BracketLeft: lambda: self.error_nav.emit(-1),
            K.Key_C: lambda: self.check_now.emit(self.current),
            K.Key_Escape: self.cancel_pick,
        }
        fn = actions.get(key)
        if fn is None:
            return False
        fn()
        return True


def describe_vector(vec):
    if not vec:
        return "not set"
    dx, dy = vec
    dist = math.hypot(dx, dy)
    bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
    names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return "%.2f m towards %s (%.0f°)" % (dist, names[int((bearing + 22.5) // 45) % 8], bearing)


class ReviewTool(QgsMapTool):
    def __init__(self, canvas, controller):
        super().__init__(canvas)
        self.ctrl = controller
        self.setCursor(Qt.CursorShape.CrossCursor)

    def canvasReleaseEvent(self, e):  # noqa: N802
        if e.button() == Qt.MouseButton.LeftButton:
            self.ctrl.on_click(e.mapPoint())

    def canvasMoveEvent(self, e):  # noqa: N802
        self.ctrl.on_move(e.mapPoint())

    def keyPressEvent(self, e):  # noqa: N802
        if self.ctrl.on_key(e.key(), e.modifiers()):
            e.accept()
        else:
            e.ignore()

    def activate(self):
        super().activate()
        self.ctrl.tool_toggled.emit(True)

    def deactivate(self):
        self.ctrl.on_tool_deactivated()
        super().deactivate()
