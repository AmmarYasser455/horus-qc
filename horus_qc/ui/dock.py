"""The Horus QC panel: two simple tabs (1 Check, 2 Review) and a ⚙ Settings window."""

import csv
import html
import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QComboBox,
    QDockWidget,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QTextBrowser,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)
from qgis.core import (
    QgsApplication,
    QgsFeatureRequest,
    QgsProject,
    QgsTask,
    QgsVectorFileWriter,
    QgsVectorLayer,
)
from qgis.gui import QgsMapLayerComboBox

from ..compat import GEOM_LINE, GEOM_POLYGON, MSG_CRITICAL, MSG_INFO, MSG_SUCCESS, MSG_WARNING, layer_filter
from ..core.catalog import CHECKS, CRITICAL, HIGH, LOW, MEDIUM, SEVERITY_COLORS, SEVERITY_RANK
from ..core.engine import Cancelled, run_job
from ..core.jobs import SCOPE_ALL, SCOPE_EXTENT, SCOPE_GRID_SELECTED, SCOPE_SELECTED, ScopeError, build_job
from ..core.review import BY_FIELD, STATUS_FIELD, TIME_FIELD, add_review_fields, has_review_fields, today_text
from ..core.settings import QcSettings
from .live import LiveChecker
from .markers import ErrorLayer, Highlighter, zoom_to
from .review import ReviewController, describe_vector

PROJECT_SCOPE = "horus_qc"
IGNORED_KEY = "ignored"
LEVELS = {0: MSG_INFO, 1: MSG_WARNING, 2: MSG_CRITICAL, 3: MSG_SUCCESS}
KEYWORDS = {
    "buildings": ("build", "bldg", "footprint", "house", "مبان", "مبنى"),
    "roads": ("road", "street", "way", "track", "طرق", "شارع"),
    "grid": ("grid", "zone", "block", "tile", "sheet", "cell", "شبك"),
}
AREA_AUTO = "auto"
AREA_CHOICES = [
    (AREA_AUTO, "Auto: my selected grid cells, else the map view"),
    (SCOPE_GRID_SELECTED, "Selected grid cells"),
    (SCOPE_EXTENT, "Current map view"),
    (SCOPE_SELECTED, "Selected features"),
    (SCOPE_ALL, "Whole layer"),
]
BIG_BUTTON = "QPushButton { font-weight: bold; font-size: 11pt; padding: 8px; }"


def severity_icon(sev, size=12):
    pm = QPixmap(size, size)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(QColor(SEVERITY_COLORS[sev])))
    p.setPen(QColor("#ffffff"))
    p.drawEllipse(1, 1, size - 2, size - 2)
    p.end()
    return QIcon(pm)


def _task_fn(task, job):
    try:
        return run_job(job, progress=lambda v, _m: task.setProgress(v), is_cancelled=task.isCanceled)
    except Cancelled:
        return None


class HorusDock(QDockWidget):
    def __init__(self, iface, plugin_dir):
        super().__init__("Horus QC")
        self.setObjectName("HorusQcDock")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.plugin_dir = plugin_dir
        self.settings = QcSettings.load()
        self.errors = []
        self.order = []  # error indexes in list order (for Prev / Next)
        self.items = {}  # error index -> tree item
        self.groups = {}  # check key -> group tree item
        self.task = None
        self._grid_watch = None
        self._fields_asked = set()
        self.error_layer = ErrorLayer()
        self.hl = Highlighter(self.canvas)
        self.review = ReviewController(iface, self.hl, lambda: self.settings)
        self.live = LiveChecker(iface, self.hl, lambda: self.settings, self.ignored_keys)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)
        top = QHBoxLayout()
        title = QLabel("<b style='font-size:12pt'>Horus QC</b>")
        self.btn_settings = QToolButton()
        self.btn_settings.setText("⚙ Settings")
        self.btn_settings.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.btn_settings.clicked.connect(self.open_settings)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(self.btn_settings)
        lay.addLayout(top)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_check_tab(), "1  Check")
        self.tabs.addTab(self._build_review_tab(), "2  Review")
        lay.addWidget(self.tabs, 1)
        self.setWidget(root)

        self.review.changed.connect(self.refresh)
        self.review.message.connect(lambda text, lvl: self.push(text, lvl))
        self.review.error_nav.connect(self.step_error)
        self.review.check_now.connect(lambda fid: fid is not None and self.live.check_now({fid}))
        self.review.tool_toggled.connect(self._on_tool_toggled)
        QgsProject.instance().cleared.connect(self._on_project_cleared)
        QgsProject.instance().readProject.connect(self._on_read_project)

        self._autodetect_layers()
        self._layers_changed()

    # ================================================================ helpers
    def push(self, text, level=0, duration=5):
        self.iface.messageBar().pushMessage("Horus QC", text, LEVELS.get(level, MSG_INFO), duration)

    def buildings(self):
        return self.cb_buildings.currentLayer()

    def roads(self):
        return self.cb_roads.currentLayer()

    def grid(self):
        return self.cb_grid.currentLayer()

    def ignored_keys(self):
        raw, _ok = QgsProject.instance().readEntry(PROJECT_SCOPE, IGNORED_KEY, "")
        try:
            return set(json.loads(raw)) if raw else set()
        except ValueError:
            return set()

    def _save_ignored(self, keys):
        QgsProject.instance().writeEntry(PROJECT_SCOPE, IGNORED_KEY, json.dumps(sorted(keys)))

    def current_scope(self):
        choice = self.cb_area.currentData()
        if choice != AREA_AUTO:
            return choice
        grid = self.grid()
        if grid is not None and grid.selectedFeatureCount() > 0:
            return SCOPE_GRID_SELECTED
        return SCOPE_EXTENT

    def _area_text(self):
        scope = self.current_scope()
        if scope == SCOPE_GRID_SELECTED:
            n = self.grid().selectedFeatureCount() if self.grid() is not None else 0
            return "Will check: %d selected grid cell%s" % (n, "" if n == 1 else "s")
        return {
            SCOPE_EXTENT: "Will check: what you see on the map",
            SCOPE_SELECTED: "Will check: the selected features",
            SCOPE_ALL: "Will check: the whole layer",
        }.get(scope, "")

    def open_settings(self):
        from ..compat import exec_dialog
        from .settings_dialog import SettingsDialog

        exec_dialog(SettingsDialog(self))
        self.refresh()

    # ================================================================ 1 Check
    def _build_check_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        form = QFormLayout()
        self.cb_buildings = QgsMapLayerComboBox()
        self.cb_buildings.setFilters(layer_filter("polygon"))
        self.cb_buildings.setAllowEmptyLayer(True)
        self.cb_roads = QgsMapLayerComboBox()
        self.cb_roads.setFilters(layer_filter("line"))
        self.cb_roads.setAllowEmptyLayer(True)
        self.cb_grid = QgsMapLayerComboBox()
        self.cb_grid.setFilters(layer_filter("polygon"))
        self.cb_grid.setAllowEmptyLayer(True)
        form.addRow("Buildings", self.cb_buildings)
        form.addRow("Roads", self.cb_roads)
        form.addRow("Grid", self.cb_grid)
        self.cb_area = QComboBox()
        for key, label in AREA_CHOICES:
            self.cb_area.addItem(label, key)
        self.cb_area.currentIndexChanged.connect(lambda *_: self.refresh())
        form.addRow("Area", self.cb_area)
        lay.addLayout(form)
        for cb in (self.cb_buildings, self.cb_roads, self.cb_grid):
            cb.layerChanged.connect(self._layers_changed)

        self.lbl_area = QLabel()
        self.lbl_area.setStyleSheet("color: gray;")
        lay.addWidget(self.lbl_area)

        self.btn_run = QPushButton("▶  Check")
        self.btn_run.setStyleSheet(BIG_BUTTON)
        self.btn_run.clicked.connect(self._run_clicked)
        lay.addWidget(self.btn_run)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.lbl_summary = QLabel("Press Check to look for problems.")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(180)
        self.tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.tree.itemSelectionChanged.connect(self._on_item_selected)
        lay.addWidget(self.tree, 1)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀ Previous")
        self.btn_next = QPushButton("Next ▶")
        self.btn_ignore = QPushButton("Ignore")
        self.btn_ignore.setToolTip("Not a real problem: hide it, also on the next check")
        self.btn_prev.clicked.connect(lambda: self.step_error(-1))
        self.btn_next.clicked.connect(lambda: self.step_error(1))
        self.btn_ignore.clicked.connect(self.ignore_selected)
        for b in (self.btn_prev, self.btn_next, self.btn_ignore):
            nav.addWidget(b)
        lay.addLayout(nav)

        self.guide = QTextBrowser()
        self.guide.setMaximumHeight(170)
        self.guide.setHtml("<p style='color:gray'>Click a problem to zoom to it and see what to do.</p>")
        lay.addWidget(self.guide)

        self.btn_export = QPushButton("Export the list…")
        self.btn_export.setFlat(True)
        self.btn_export.clicked.connect(self.export_errors)
        lay.addWidget(self.btn_export, 0, Qt.AlignmentFlag.AlignRight)
        self._update_buttons()
        return w

    def _autodetect_layers(self):
        """Pre-select layers whose names look like buildings / roads / grid."""
        layers = [lyr for lyr in QgsProject.instance().mapLayers().values() if isinstance(lyr, QgsVectorLayer)]

        def find(kind, combo, gtype):
            for lyr in layers:
                if lyr.geometryType() == gtype and any(k in lyr.name().lower() for k in KEYWORDS[kind]):
                    combo.setLayer(lyr)
                    return True
            return False

        find("buildings", self.cb_buildings, GEOM_POLYGON)
        if not find("roads", self.cb_roads, GEOM_LINE):
            self.cb_roads.setLayer(None)
        if not find("grid", self.cb_grid, GEOM_POLYGON) or self.cb_grid.currentLayer() is self.cb_buildings.currentLayer():
            self.cb_grid.setLayer(None)

    def _layers_changed(self, *_):
        if self._grid_watch is not None:
            try:
                self._grid_watch.selectionChanged.disconnect(self._grid_selection_changed)
            except (TypeError, RuntimeError):
                pass
        self._grid_watch = self.grid()
        if self._grid_watch is not None:
            self._grid_watch.selectionChanged.connect(self._grid_selection_changed)
        self.review.set_layers(self.buildings(), self.grid())
        self.live.watch(self.buildings(), self.roads(), self.grid())
        self.refresh()

    def _grid_selection_changed(self, *_):
        self.refresh()

    # ---------------------------------------------------------------- run
    def _run_clicked(self):
        if self.task is not None:
            self.task.cancel()
        else:
            self.run_checks()

    def run_checks(self):
        if self.task is not None:
            return
        try:
            job = build_job(self.settings, self.buildings(), self.roads(), self.grid(),
                            self.current_scope(), self.canvas, ignored=self.ignored_keys())
        except ScopeError as e:
            self.push(str(e), 1)
            return
        n = (len(job.buildings.scope) if job.buildings else 0) + (len(job.roads.scope) if job.roads else 0)
        if n == 0:
            self.push("Nothing to check here. Zoom to your area or select your grid cell.", 1)
            return
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.btn_run.setText("■  Stop")
        self.lbl_summary.setText("Checking %d features…" % n)
        self.task = QgsTask.fromFunction("Horus QC check", _task_fn, job, on_finished=self._on_task_done)
        self.task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        QgsApplication.taskManager().addTask(self.task)

    def _on_task_done(self, exception, result=None):
        self.task = None
        self.progress.setVisible(False)
        self.btn_run.setText("▶  Check")
        if exception is not None:
            self.lbl_summary.setText("The check failed: %s" % exception)
            self.push("Check failed: %s" % exception, 2)
            return
        if result is None:
            self.lbl_summary.setText("Check stopped.")
            return
        self.set_errors(result)

    # ---------------------------------------------------------------- results list
    def set_errors(self, errors):
        self.errors = errors
        self.hl._drop(self.hl.error_bands)
        self.error_layer.rebuild(errors, self.canvas.mapSettings().destinationCrs())
        self.tree.blockSignals(True)
        self.tree.clear()
        self.order, self.items, self.groups = [], {}, {}
        by_check = {}
        for i, e in enumerate(errors):
            if e.status == "open":
                by_check.setdefault(e.check, []).append(i)
        for check in sorted(by_check, key=lambda k: (SEVERITY_RANK[CHECKS[k].severity], CHECKS[k].title)):
            d = CHECKS[check]
            top = QTreeWidgetItem()
            top.setIcon(0, severity_icon(d.severity))
            f = QFont()
            f.setBold(True)
            top.setFont(0, f)
            top.setToolTip(0, d.why)
            self.tree.addTopLevelItem(top)
            self.groups[check] = top
            for i in by_check[check]:
                e = errors[i]
                it = QTreeWidgetItem(["#%s  %s" % (e.fid, e.message)])
                it.setData(0, Qt.ItemDataRole.UserRole, i)
                it.setToolTip(0, e.message)
                top.addChild(it)
                self.items[i] = it
                self.order.append(i)
            self._label_group(check)
        self.tree.blockSignals(False)
        self._update_summary()
        if self.order:
            self._select(self.order[0])
        else:
            self.guide.setHtml("<p style='color:#2e7d32'><b>No problems found.</b></p>")
        self._update_buttons()

    def _label_group(self, check):
        top = self.groups.get(check)
        if top is not None:
            top.setText(0, "%s  (%d)" % (CHECKS[check].title, top.childCount()))

    def _update_summary(self):
        open_errs = [e for e in self.errors if e.status == "open"]
        if not open_errs:
            self.lbl_summary.setText("<b style='color:#2e7d32'>✔ No problems found.</b>")
            return
        parts = []
        for sev in (CRITICAL, HIGH, MEDIUM, LOW):
            n = sum(1 for e in open_errs if e.severity == sev)
            if n:
                parts.append("<span style='color:%s'>●</span> %d %s" % (SEVERITY_COLORS[sev], n, sev))
        self.lbl_summary.setText("<b>%d problem%s</b> &nbsp; %s" % (
            len(open_errs), "" if len(open_errs) == 1 else "s", " &nbsp;".join(parts)))

    def selected_error(self):
        items = self.tree.selectedItems()
        if not items:
            return None, None
        i = items[0].data(0, Qt.ItemDataRole.UserRole)
        if i is None:
            return None, None
        return i, self.errors[i]

    def _select(self, index):
        it = self.items.get(index)
        if it is None:
            return
        it.parent().setExpanded(True)
        self.tree.setCurrentItem(it)
        self.tree.scrollToItem(it)

    def _on_item_selected(self):
        self._update_buttons()
        i, err = self.selected_error()
        if err is None:
            items = self.tree.selectedItems()
            if items and items[0].childCount():
                items[0].setExpanded(True)
            return
        self.hl.show_error(err)
        self._show_guidance(err)
        layer = QgsProject.instance().mapLayer(err.layer_id)
        if layer is not None and err.geometry is not None and not err.geometry.isNull():
            zoom_to(self.canvas, err.geometry, layer.crs())
        elif layer is not None:
            f = layer.getFeature(err.fid)
            if f.isValid() and f.hasGeometry():
                zoom_to(self.canvas, f.geometry(), layer.crs())
        if layer is not None:
            layer.selectByIds([err.fid])

    def _update_buttons(self):
        _i, err = self.selected_error()
        self.btn_ignore.setEnabled(err is not None)
        self.btn_prev.setEnabled(bool(self.order))
        self.btn_next.setEnabled(bool(self.order))
        self.btn_export.setEnabled(bool(self.errors))

    def _show_guidance(self, err):
        d = err.definition
        steps = "".join("<li>%s</li>" % html.escape(s) for s in d.steps)
        other = " and <span style='color:#d633ff'>#%s</span> (dashed)" % err.fid2 if err.fid2 is not None else ""
        self.guide.setHtml(
            "<p><span style='color:%s'>●</span> <b>%s</b>: <span style='color:#ff8c00'>#%s</span>%s</p>"
            "<p>%s</p><p style='color:gray'>%s</p><p><b>What to do</b></p><ul>%s</ul>"
            % (SEVERITY_COLORS[d.severity], html.escape(d.title), err.fid, other,
               html.escape(err.message), html.escape(d.why), steps))

    def step_error(self, step):
        if not self.order:
            return
        i, _err = self.selected_error()
        pos = self.order.index(i) if i in self.order else -1
        pos = max(0, min(len(self.order) - 1, pos + step)) if pos >= 0 else 0
        self._select(self.order[pos])

    def ignore_selected(self):
        i, err = self.selected_error()
        if err is None:
            return
        keys = self.ignored_keys()
        keys.add(err.key)
        self._save_ignored(keys)
        err.status = "ignored"
        self.error_layer.remove(i)
        pos = self.order.index(i)
        self.order.remove(i)
        it = self.items.pop(i)
        group = it.parent()
        group.removeChild(it)
        if group.childCount() == 0:
            self.tree.takeTopLevelItem(self.tree.indexOfTopLevelItem(group))
            self.groups.pop(err.check, None)
        else:
            self._label_group(err.check)
        self._update_summary()
        if self.order:
            self._select(self.order[min(pos, len(self.order) - 1)])
        else:
            self.hl._drop(self.hl.error_bands)
            self.guide.setHtml("<p style='color:#2e7d32'><b>All done.</b></p>")
        self._update_buttons()

    def export_errors(self):
        path, _flt = QFileDialog.getSaveFileName(self, "Export the list", "horus_qc_problems.csv",
                                                 "CSV (*.csv);;GeoPackage (*.gpkg)")
        if not path:
            return
        try:
            if path.lower().endswith(".gpkg"):
                self._export_gpkg(path)
            else:
                if not path.lower().endswith(".csv"):
                    path += ".csv"
                self._export_csv(path)
        except Exception as e:
            self.push("Export failed: %s" % e, 2)
            return
        self.push("Exported %d problems to %s" % (len(self.errors), path), 3)

    def _export_csv(self, path):
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["severity", "check", "title", "layer", "fid", "other_fid", "message", "value", "status", "x", "y"])
            for e in self.errors:
                pt = e.geometry.centroid().asPoint() if e.geometry is not None and not e.geometry.isNull() else None
                w.writerow([e.severity, e.check, CHECKS[e.check].title, e.layer_name, e.fid,
                            "" if e.fid2 is None else e.fid2, e.message,
                            "" if e.value is None else round(float(e.value), 3), e.status,
                            "" if pt is None else round(pt.x(), 8), "" if pt is None else round(pt.y(), 8)])

    def _export_gpkg(self, path):
        lyr = self.error_layer.layer()
        if lyr is None:
            raise RuntimeError("No problem layer to export.")
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = "horus_qc_problems"
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, path, QgsProject.instance().transformContext(), opts)
        if int(getattr(res[0], "value", res[0])) != 0:
            raise RuntimeError(res[1])

    # ================================================================ 2 Review
    def _build_review_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        day = QGroupBox("Today")
        dl = QVBoxLayout(day)
        self.pb_today = QProgressBar()
        self.pb_today.setFormat("%v / %m buildings")
        self.lbl_stats = QLabel()
        dl.addWidget(self.pb_today)
        dl.addWidget(self.lbl_stats)
        lay.addWidget(day)

        self.btn_review = QPushButton("▶  Start review")
        self.btn_review.setCheckable(True)
        self.btn_review.setStyleSheet(BIG_BUTTON)
        self.btn_review.toggled.connect(self._toggle_review)
        lay.addWidget(self.btn_review)
        self.lbl_pos = QLabel()
        self.lbl_pos.setStyleSheet("color: gray;")
        self.lbl_pos.setWordWrap(True)
        lay.addWidget(self.lbl_pos)

        lean = QGroupBox("Lean (roof → base)")
        ll = QVBoxLayout(lean)
        self.lbl_vector = QLabel()
        self.lbl_vector.setWordWrap(True)
        self.btn_pick = QPushButton("Pick the lean  (V)")
        self.btn_pick.setToolTip("Click a roof corner, then the same corner at the base of the building")
        self.btn_pick.clicked.connect(self.review.start_pick)
        ll.addWidget(self.lbl_vector)
        ll.addWidget(self.btn_pick)
        lay.addWidget(lean)

        keys = QLabel(
            "<table cellspacing=4>"
            "<tr><td><b>1–9</b></td><td>move to base (number of floors)</td></tr>"
            "<tr><td><b>W A S D</b></td><td>small move &nbsp;(Shift = bigger)</td></tr>"
            "<tr><td><b>E</b></td><td>approve → next</td></tr>"
            "<tr><td><b>F</b></td><td>flag a problem → next</td></tr>"
            "<tr><td><b>N / Q</b></td><td>next / back</td></tr>"
            "<tr><td><b>Ctrl+Z</b></td><td>undo</td></tr>"
            "</table>")
        keys.setStyleSheet("background: palette(base); border-radius: 4px; padding: 6px;")
        lay.addWidget(keys)
        hint = QLabel("Click the map once after starting so it receives your keys.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: gray;")
        lay.addWidget(hint)
        lay.addStretch(1)

        b_rep = QPushButton("Export today's report…")
        b_rep.setFlat(True)
        b_rep.clicked.connect(self.export_report)
        lay.addWidget(b_rep, 0, Qt.AlignmentFlag.AlignRight)
        return w

    def add_review_fields(self, ask=True):
        lyr = self.buildings()
        if lyr is None:
            return False
        if ask:
            ans = QMessageBox.question(
                self, "Horus QC",
                "Save who reviewed each building, and when, in '%s'?\n\n"
                "This adds three fields: %s, %s, %s." % (lyr.name(), STATUS_FIELD, BY_FIELD, TIME_FIELD))
            if ans != QMessageBox.StandardButton.Yes:
                return False
        ok = add_review_fields(lyr)
        self.push("Review status will be saved in the layer." if ok else
                  "Couldn't add the fields (read-only layer?). Review status is kept until QGIS closes.", 3 if ok else 1)
        self.refresh()
        return ok

    def build_queue(self):
        lyr = self.buildings()
        if lyr is None:
            self.push("Choose your buildings layer on the Check tab first.", 1)
            return 0
        try:
            job = build_job(self.settings, lyr, None, self.grid(), self.current_scope(), self.canvas, enabled=set())
        except ScopeError as e:
            self.push(str(e), 1)
            return 0
        n = self.review.set_queue(sorted(job.buildings.scope))
        self.refresh()
        return n

    def start_review(self, ask_fields=True):
        lyr = self.buildings()
        if lyr is None:
            self.push("Choose your buildings layer on the Check tab first.", 1)
            return False
        if ask_fields and not has_review_fields(lyr) and lyr.id() not in self._fields_asked:
            self._fields_asked.add(lyr.id())
            self.add_review_fields(ask=True)
        if not self.build_queue():
            self.push("No buildings in this area. Zoom to your grid or select your grid cell.", 1)
            return False
        self.push("%d buildings to review." % self.review.remaining(), 0, 3)
        self.review.activate()
        return True

    def _toggle_review(self, on):
        if on:
            if not self.start_review():
                self._on_tool_toggled(False)
        else:
            self.review.deactivate()

    def _on_tool_toggled(self, active):
        self.btn_review.blockSignals(True)
        self.btn_review.setChecked(active)
        self.btn_review.setText("■  Stop review" if active else "▶  Start review")
        self.btn_review.blockSignals(False)

    def refresh(self, *_):
        """Update every label from the current state."""
        self.lbl_area.setText(self._area_text())
        st = self.review.stats
        target = max(1, self.settings.daily_target)
        self.pb_today.setMaximum(target)
        self.pb_today.setValue(min(target, st.reviewed))
        c = st.counts
        self.lbl_stats.setText("✔ %d approved   ·   ⚑ %d flagged   ·   ↘ %d moved"
                               % (c["approved"], c["flagged"], c["shifted"]))
        if self.review.queue:
            pos = self.review.pos + 1 if self.review.pos >= 0 else 0
            self.lbl_pos.setText("Building %d of %d · %d left" % (pos, len(self.review.queue), self.review.remaining()))
        else:
            self.lbl_pos.setText(self._area_text().replace("Will check", "Will review"))
        vec, _key = self.review.vector_for(self.review.current)
        self.lbl_vector.setText("<b>%s</b> per floor" % describe_vector(vec) if vec else
                                "<i>Not set yet. Click the button, then click a roof corner and the same "
                                "corner at the base.</i>")

    def export_report(self):
        lyr = self.buildings()
        if lyr is None or not has_review_fields(lyr):
            self.push("The report needs the review fields: answer Yes when Start review asks, "
                      "or use ⚙ Settings › Save review status in the buildings layer.", 1)
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export today's report",
                                              "horus_review_%s.csv" % today_text(), "CSV (*.csv)")
        if not path:
            return
        day = today_text()
        expr = "\"%s\" LIKE '%s%%'" % (TIME_FIELD, day)
        rows, per_person = [], {}
        for f in lyr.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
            status, who, when = f[STATUS_FIELD], f[BY_FIELD], f[TIME_FIELD]
            rows.append([f.id(), status, who, when])
            per_person.setdefault(str(who), {}).setdefault(str(status), 0)
            per_person[str(who)][str(status)] += 1
        with open(path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["fid", "status", "reviewer", "time"])
            w.writerows(rows)
        summary_path = os.path.splitext(path)[0] + "_summary.csv"
        with open(summary_path, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.writer(fh)
            w.writerow(["date", "reviewer", "approved", "flagged", "total"])
            for who, counts in sorted(per_person.items()):
                w.writerow([day, who, counts.get("approved", 0), counts.get("flagged", 0), sum(counts.values())])
        self.push("Report saved: %d buildings reviewed today (%s)" % (len(rows), path), 3)

    # ================================================================ lifecycle
    def _on_read_project(self, *_):
        self.review.load_vectors()
        self._autodetect_layers()
        self.refresh()

    def _on_project_cleared(self):
        self.errors, self.order, self.items, self.groups = [], [], {}, {}
        self.error_layer.layer_id = None
        self.hl.clear()
        self.review.queue, self.review.pos, self.review.current = [], -1, None
        self.review.vectors = {}
        self.tree.clear()
        self.lbl_summary.setText("Press Check to look for problems.")
        self.refresh()

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
        self.live.stop()
        self.review.deactivate()
        self.review.set_layers(None, None)
        if self._grid_watch is not None:
            try:
                self._grid_watch.selectionChanged.disconnect(self._grid_selection_changed)
            except (TypeError, RuntimeError):
                pass
        self.hl.clear()
        self.error_layer.clear()
        project = QgsProject.instance()
        for sig, slot in ((project.cleared, self._on_project_cleared), (project.readProject, self._on_read_project)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
