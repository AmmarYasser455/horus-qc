"""Instant checks: re-check a building or road a moment after each edit."""

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal

from ..compat import MSG_INFO, MSG_WARNING
from ..core.catalog import CHECKS
from ..core.jobs import live_check


class LiveChecker(QObject):
    found = pyqtSignal(list)

    def __init__(self, iface, highlighter, settings_getter, ignored_getter):
        super().__init__()
        self.iface = iface
        self.hl = highlighter
        self.settings = settings_getter
        self.ignored = ignored_getter
        self.buildings = self.roads = self.grid = None
        self._pending_b, self._pending_r = set(), set()
        self._connections = []
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self._run)

    def watch(self, buildings, roads, grid):
        self.stop()
        self.buildings, self.roads, self.grid = buildings, roads, grid
        for layer, bucket in ((buildings, self._pending_b), (roads, self._pending_r)):
            if layer is None:
                continue
            slot_geom = (lambda fid, _g, b=bucket: self._queue(b, fid))
            slot_add = (lambda fid, b=bucket: self._queue(b, fid))
            layer.geometryChanged.connect(slot_geom)
            layer.featureAdded.connect(slot_add)
            self._connections.append((layer, slot_geom, slot_add))

    def stop(self):
        for layer, slot_geom, slot_add in self._connections:
            try:
                layer.geometryChanged.disconnect(slot_geom)
                layer.featureAdded.disconnect(slot_add)
            except (TypeError, RuntimeError):
                pass
        self._connections = []
        self._pending_b.clear()
        self._pending_r.clear()
        self._timer.stop()

    def _queue(self, bucket, fid):
        if not self.settings().live_check:
            return
        bucket.add(fid)
        self._timer.start()

    def check_now(self, building_fids=None, road_fids=None, quiet_if_ok=False):
        try:
            errs = live_check(self.settings(), self.buildings, self.roads, self.grid,
                              set(building_fids or ()), set(road_fids or ()), self.ignored())
        except Exception as e:  # never let a live check break editing
            self.iface.messageBar().pushMessage("Horus QC", "Live check failed: %s" % e, MSG_WARNING, 5)
            return []
        if errs:
            items = []
            for e in errs:
                layer = self.buildings if self.buildings is not None and e.layer_id == self.buildings.id() else self.roads
                if layer is not None and e.geometry is not None and not e.geometry.isNull():
                    items.append((e.geometry, layer.crs()))
            self.hl.flash(items)
            lines = ["%s (#%s): %s" % (CHECKS[e.check].title, e.fid, e.message) for e in errs[:3]]
            more = " (+%d more)" % (len(errs) - 3) if len(errs) > 3 else ""
            self.iface.messageBar().pushMessage("Horus QC", " · ".join(lines) + more, MSG_WARNING, 6)
        elif not quiet_if_ok:
            self.iface.messageBar().pushMessage("Horus QC", "No problems found on this feature.", MSG_INFO, 2)
        self.found.emit(errs)
        return errs

    def _run(self):
        b, r = set(self._pending_b), set(self._pending_r)
        self._pending_b.clear()
        self._pending_r.clear()
        if b or r:
            self.check_now(b, r, quiet_if_ok=True)
