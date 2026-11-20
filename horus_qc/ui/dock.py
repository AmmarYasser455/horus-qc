"""The Horus QC dock: Check, Review and Settings tabs."""

import csv
import html
import json
import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPixmap
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
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
from ..core.catalog import (
    BUILDING_CHECKS,
    CHECKS,
    CRITICAL,
    HIGH,
    LOW,
    MEDIUM,
    ROAD_CHECKS,
    SEVERITY_COLORS,
)
from ..core.engine import Cancelled, run_job
from ..core.fixes import FixError, apply_fix
from ..core.jobs import SCOPE_ALL, SCOPE_EXTENT, SCOPE_LABELS, ScopeError, build_job
from ..core.review import (
    BY_FIELD,
    STATUS_FIELD,
    TIME_FIELD,
    add_review_fields,
    has_review_fields,
    today_text,
)
from ..core.settings import SETTING_SPECS, QcSettings
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
        self.visible_rows = []
        self.task = None
        self.error_layer = ErrorLayer()
        self.hl = Highlighter(self.canvas)
        self.review = ReviewController(iface, self.hl, lambda: self.settings)
        self.live = LiveChecker(iface, self.hl, lambda: self.settings, self.ignored_keys)

        tabs = QTabWidget()
        tabs.addTab(self._scroll(self._build_check_tab()), "Check")
        tabs.addTab(self._scroll(self._build_review_tab()), "Review")
        tabs.addTab(self._scroll(self._build_settings_tab()), "Settings")
        self.setWidget(tabs)

        self.review.changed.connect(self._update_review_panel)
        self.review.message.connect(lambda text, lvl: self.push(text, lvl))
        self.review.error_nav.connect(self.step_error)
        self.review.check_now.connect(lambda fid: fid is not None and self.live.check_now({fid}))
        self.review.tool_toggled.connect(self._on_tool_toggled)
        QgsProject.instance().cleared.connect(self._on_project_cleared)
        QgsProject.instance().readProject.connect(self._on_read_project)

        self._autodetect_layers()
        self._layers_changed()

    # ================================================================ helpers
    def _scroll(self, widget):
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setWidget(widget)
        return sa

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

    # ================================================================ Check tab
    def _build_check_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        box = QGroupBox("Layers")
        form = QFormLayout(box)
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
        form.addRow("Grid / zones", self.cb_grid)
        for cb in (self.cb_buildings, self.cb_roads, self.cb_grid):
            cb.layerChanged.connect(self._layers_changed)
        self.cb_scope = QComboBox()
        for key, label in SCOPE_LABELS:
            self.cb_scope.addItem(label, key)
        self.cb_scope.setCurrentIndex(self.cb_scope.findData(SCOPE_EXTENT))
        form.addRow("Check", self.cb_scope)
        lay.addWidget(box)

        row = QHBoxLayout()
        self.btn_run = QPushButton("▶  Run checks")
        self.btn_run.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_run.clicked.connect(self.run_checks)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_checks)
        row.addWidget(self.btn_run, 1)
        row.addWidget(self.btn_cancel)
        lay.addLayout(row)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)

        self.checks_box = QGroupBox("Checks")
        self.checks_box.setCheckable(False)
        cl = QVBoxLayout(self.checks_box)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(60)
        self.tree.setMaximumHeight(150)
        for title, defs in (("Buildings", BUILDING_CHECKS), ("Roads", ROAD_CHECKS)):
            top = QTreeWidgetItem([title])
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsAutoTristate)
            self.tree.addTopLevelItem(top)
            for d in defs:
                it = QTreeWidgetItem([d.title])
                it.setIcon(0, severity_icon(d.severity))
                it.setData(0, Qt.ItemDataRole.UserRole, d.key)
                it.setToolTip(0, d.why)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(0, Qt.CheckState.Checked if d.key in self.settings.enabled_checks else Qt.CheckState.Unchecked)
                top.addChild(it)
            top.setExpanded(False)
        self.tree.itemChanged.connect(self._checks_changed)
        cl.addWidget(self.tree)
        lay.addWidget(self.checks_box)

        self.lbl_summary = QLabel("Run the checks to see results.")
        self.lbl_summary.setWordWrap(True)
        lay.addWidget(self.lbl_summary)

        frow = QHBoxLayout()
        self.cb_sev = QComboBox()
        self.cb_sev.addItem("All severities", "")
        for sev in (CRITICAL, HIGH, MEDIUM, LOW):
            self.cb_sev.addItem(severity_icon(sev), sev.capitalize(), sev)
        self.cb_type = QComboBox()
        self.cb_type.addItem("All checks", "")
        self.ed_search = QLineEdit()
        self.ed_search.setPlaceholderText("Search…")
        self.chk_closed = QCheckBox("Show fixed/ignored")
        for wdg in (self.cb_sev, self.cb_type):
            wdg.currentIndexChanged.connect(self._fill_table)
        self.ed_search.textChanged.connect(self._fill_table)
        self.chk_closed.toggled.connect(self._fill_table)
        frow.addWidget(self.cb_sev)
        frow.addWidget(self.cb_type, 1)
        lay.addLayout(frow)
        frow2 = QHBoxLayout()
        frow2.addWidget(self.ed_search, 1)
        frow2.addWidget(self.chk_closed)
        lay.addLayout(frow2)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Severity", "Check", "Layer", "ID", "Detail"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Interactive)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(1, 130)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(200)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.cellDoubleClicked.connect(lambda *_: self.zoom_selected())
        lay.addWidget(self.table, 1)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀ Prev")
        self.btn_next = QPushButton("Next ▶")
        self.btn_zoom = QPushButton("Zoom")
        self.btn_select = QPushButton("Select")
        self.btn_prev.clicked.connect(lambda: self.step_error(-1))
        self.btn_next.clicked.connect(lambda: self.step_error(1))
        self.btn_zoom.clicked.connect(self.zoom_selected)
        self.btn_select.clicked.connect(self.select_feature)
        for b in (self.btn_prev, self.btn_next, self.btn_zoom, self.btn_select):
            nav.addWidget(b)
        lay.addLayout(nav)

        act = QHBoxLayout()
        self.btn_fix = QPushButton("Fix")
        self.btn_fix.setStyleSheet("font-weight: bold;")
        self.btn_fix_all = QPushButton("Fix all of this type")
        self.btn_ignore = QPushButton("Ignore")
        self.btn_export = QPushButton("Export…")
        self.btn_fix.clicked.connect(self.fix_selected)
        self.btn_fix_all.clicked.connect(self.fix_all_of_type)
        self.btn_ignore.clicked.connect(self.ignore_selected)
        self.btn_export.clicked.connect(self.export_errors)
        for b in (self.btn_fix, self.btn_fix_all, self.btn_ignore, self.btn_export):
            act.addWidget(b)
        lay.addLayout(act)

        self.guide = QTextBrowser()
        self.guide.setOpenExternalLinks(True)
        self.guide.setMinimumHeight(170)
        self.guide.setHtml("<p style='color:gray'>Select an error to see what's wrong and how to fix it.</p>")
        lay.addWidget(self.guide)
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
        self.review.set_layers(self.buildings(), self.grid())
        self.live.watch(self.buildings(), self.roads(), self.grid())
        self._update_review_panel()

    def _checks_changed(self, item, _col):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.settings.enabled_checks.add(key)
        else:
            self.settings.enabled_checks.discard(key)
        self.settings.save()

    # ---------------------------------------------------------------- run
    def run_checks(self):
        if self.task is not None:
            return
        try:
            job = build_job(self.settings, self.buildings(), self.roads(), self.grid(),
                            self.cb_scope.currentData(), self.canvas, ignored=self.ignored_keys())
        except ScopeError as e:
            self.push(str(e), 1)
            return
        n = (len(job.buildings.scope) if job.buildings else 0) + (len(job.roads.scope) if job.roads else 0)
        if n == 0:
            self.push("Nothing to check in this scope.", 1)
            return
        self.progress.setValue(0)
        self.progress.setVisible(True)
        self.btn_run.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.lbl_summary.setText("Checking %d features…" % n)
        self.task = QgsTask.fromFunction("Horus QC checks", _task_fn, job, on_finished=self._on_task_done)
        self.task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        QgsApplication.taskManager().addTask(self.task)

    def cancel_checks(self):
        if self.task is not None:
            self.task.cancel()

    def _on_task_done(self, exception, result=None):
        self.task = None
        self.progress.setVisible(False)
        self.btn_run.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        if exception is not None:
            self.lbl_summary.setText("The check failed: %s" % exception)
            self.push("Check failed: %s" % exception, 2)
            return
        if result is None:
            self.lbl_summary.setText("Check cancelled.")
            return
        self.set_errors(result)

    def set_errors(self, errors):
        self.errors = errors
        self.hl._drop(self.hl.error_bands)
        self.error_layer.rebuild(errors, self.canvas.mapSettings().destinationCrs())
        types = sorted({e.check for e in errors}, key=lambda k: CHECKS[k].title)
        self.cb_type.blockSignals(True)
        self.cb_type.clear()
        self.cb_type.addItem("All checks", "")
        for k in types:
            self.cb_type.addItem(severity_icon(CHECKS[k].severity), CHECKS[k].title, k)
        self.cb_type.blockSignals(False)
        self._fill_table()
        self._update_summary()
        if self.visible_rows:
            self.table.selectRow(0)

    def _update_summary(self):
        open_errs = [e for e in self.errors if e.status == "open"]
        if not self.errors:
            self.lbl_summary.setText("<b style='color:#2e7d32'>No problems found.</b>")
            return
        parts = []
        for sev in (CRITICAL, HIGH, MEDIUM, LOW):
            n = sum(1 for e in open_errs if e.severity == sev)
            if n:
                parts.append("<span style='color:%s'>●</span> %d %s" % (SEVERITY_COLORS[sev], n, sev))
        fixed = sum(1 for e in self.errors if e.status == "fixed")
        ignored = sum(1 for e in self.errors if e.status == "ignored")
        tail = " · %d fixed · %d ignored" % (fixed, ignored) if fixed or ignored else ""
        self.lbl_summary.setText("<b>%d open</b>  %s%s" % (len(open_errs), "  ".join(parts), tail))

    # ---------------------------------------------------------------- table
    def _fill_table(self, *_):
        sev = self.cb_sev.currentData()
        typ = self.cb_type.currentData()
        text = self.ed_search.text().strip().lower()
        show_closed = self.chk_closed.isChecked()
        rows = []
        for i, e in enumerate(self.errors):
            if e.status != "open" and not show_closed:
                continue
            if sev and e.severity != sev:
                continue
            if typ and e.check != typ:
                continue
            if text and text not in ("%s %s %s %s" % (CHECKS[e.check].title, e.message, e.fid, e.layer_name)).lower():
                continue
            rows.append(i)
        self.visible_rows = rows
        self.table.setUpdatesEnabled(False)
        self.table.blockSignals(True)
        self.table.setRowCount(len(rows))
        for r, i in enumerate(rows):
            e = self.errors[i]
            cells = [e.severity.capitalize(), CHECKS[e.check].title, e.layer_name, str(e.fid), e.message]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setData(Qt.ItemDataRole.UserRole, i)
                it.setToolTip("%s: %s" % (CHECKS[e.check].title, e.message))
                if c == 0:
                    it.setIcon(severity_icon(e.severity))
                if e.status != "open":
                    f = QFont()
                    f.setStrikeOut(True)
                    it.setFont(f)
                    it.setForeground(QBrush(QColor("#888888")))
                    if c == 0:
                        it.setText(e.status.capitalize())
                self.table.setItem(r, c, it)
        self.table.blockSignals(False)
        self.table.setUpdatesEnabled(True)
        self._update_buttons()

    def selected_error(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None, None
        item = self.table.item(rows[0].row(), 0)
        if item is None:
            return None, None
        i = item.data(Qt.ItemDataRole.UserRole)
        return i, self.errors[i]

    def _on_row_selected(self):
        i, err = self.selected_error()
        self._update_buttons()
        if err is None:
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

    def _update_buttons(self):
        _i, err = self.selected_error()
        has = err is not None
        fixable = has and err.status == "open" and bool(err.definition.autofix)
        self.btn_fix.setEnabled(fixable)
        self.btn_fix_all.setEnabled(fixable)
        self.btn_fix.setToolTip(err.definition.autofix_label if fixable else "No automatic fix for this error")
        self.btn_ignore.setEnabled(has and err.status == "open")
        for b in (self.btn_zoom, self.btn_select):
            b.setEnabled(has)
        self.btn_prev.setEnabled(bool(self.visible_rows))
        self.btn_next.setEnabled(bool(self.visible_rows))
        self.btn_export.setEnabled(bool(self.errors))

    def _show_guidance(self, err):
        d = err.definition
        color = SEVERITY_COLORS[d.severity]
        steps = "".join("<li>%s</li>" % html.escape(s) for s in d.steps)
        fix = ("<p><b>One-click fix:</b> %s <i>(undo with Ctrl+Z)</i></p>" % html.escape(d.autofix_label)
               if d.autofix else "<p><i>No automatic fix: this needs your judgement against the imagery.</i></p>")
        other = ""
        if err.fid2 is not None:
            other = " &nbsp;·&nbsp; with <span style='color:#d633ff'>#%s</span> (dashed)" % err.fid2
        self.guide.setHtml(
            "<p><span style='background:%s;color:white;padding:2px 6px;border-radius:3px'>&nbsp;%s&nbsp;</span>"
            " <b>%s</b></p>"
            "<p><b>Feature <span style='color:#ff8c00'>#%s</span></b> in %s%s<br>%s</p>"
            "<p>%s</p><p><b>How to fix:</b></p><ol>%s</ol>%s"
            % (color, d.severity.upper(), html.escape(d.title), err.fid, html.escape(err.layer_name), other,
               html.escape(err.message), html.escape(d.why), steps, fix))

    def step_error(self, step):
        if not self.visible_rows:
            return
        rows = self.table.selectionModel().selectedRows()
        r = rows[0].row() if rows else -1
        r = max(0, min(len(self.visible_rows) - 1, r + step)) if r >= 0 else 0
        self.table.selectRow(r)

    def zoom_selected(self):
        _i, err = self.selected_error()
        if err is None:
            return
        layer = QgsProject.instance().mapLayer(err.layer_id)
        if layer is None:
            return
        f = layer.getFeature(err.fid)
        geom = f.geometry() if f.isValid() and f.hasGeometry() else err.geometry
        zoom_to(self.canvas, geom, layer.crs(), min_size_m=40)

    def select_feature(self):
        _i, err = self.selected_error()
        if err is None:
            return
        layer = QgsProject.instance().mapLayer(err.layer_id)
        if layer is not None:
            layer.selectByIds([err.fid])
            self.iface.setActiveLayer(layer)

    # ---------------------------------------------------------------- actions
    def _close_error(self, index, status):
        err = self.errors[index]
        err.status = status
        self.error_layer.remove(index)
        row = self.table.currentRow()
        self._fill_table()
        self._update_summary()
        if self.visible_rows:
            self.table.selectRow(min(max(row, 0), len(self.visible_rows) - 1))
        else:
            self.hl._drop(self.hl.error_bands)

    def fix_selected(self):
        i, err = self.selected_error()
        if err is None:
            return
        try:
            msg = apply_fix(err, self.settings)
        except FixError as e:
            self.push(str(e), 1)
            return
        except Exception as e:  # keep the panel usable whatever happens
            self.push("Fix failed: %s" % e, 2)
            return
        self.review.stats.bump("fixed")
        self.push(msg, 3, 3)
        self._close_error(i, "fixed")
        self.review.refresh_highlight()
        self._update_review_panel()

    def fix_all_of_type(self):
        _i, err = self.selected_error()
        if err is None:
            return
        todo = [(k, e) for k, e in enumerate(self.errors) if e.check == err.check and e.status == "open"]
        ans = QMessageBox.question(
            self, "Horus QC",
            "Apply '%s' to %d '%s' errors?\n\nEvery fix goes to the layer's undo history; "
            "nothing is saved until you save the layer edits." % (err.definition.autofix_label, len(todo), err.definition.title))
        if ans != QMessageBox.StandardButton.Yes:
            return
        ok = failed = 0
        for k, e in todo:
            try:
                apply_fix(e, self.settings)
                self.error_layer.remove(k)
                ok += 1
            except Exception:
                failed += 1
        self.review.stats.bump("fixed", ok)
        self._fill_table()
        self._update_summary()
        self._update_review_panel()
        self.push("Fixed %d, could not fix %d. Review the changes, then save the layer." % (ok, failed), 3 if not failed else 1)

    def ignore_selected(self):
        i, err = self.selected_error()
        if err is None:
            return
        keys = self.ignored_keys()
        keys.add(err.key)
        self._save_ignored(keys)
        self._close_error(i, "ignored")

    def export_errors(self):
        path, _flt = QFileDialog.getSaveFileName(self, "Export errors", "horus_qc_errors.csv",
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
        self.push("Exported %d errors to %s" % (len(self.errors), path), 3)

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
            raise RuntimeError("No error layer to export.")
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = "horus_qc_errors"
        res = QgsVectorFileWriter.writeAsVectorFormatV3(lyr, path, QgsProject.instance().transformContext(), opts)
        if int(getattr(res[0], "value", res[0])) != 0:
            raise RuntimeError(res[1])

    # ================================================================ Review tab
    def _build_review_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)

        day = QGroupBox("Today")
        dl = QFormLayout(day)
        self.ed_reviewer = QLineEdit(self.settings.reviewer)
        self.ed_reviewer.editingFinished.connect(self._save_reviewer)
        self.sp_target = QSpinBox()
        self.sp_target.setRange(1, 100000)
        self.sp_target.setValue(self.settings.daily_target)
        self.sp_target.valueChanged.connect(self._save_target)
        self.pb_today = QProgressBar()
        self.pb_today.setFormat("%v / %m buildings")
        self.lbl_stats = QLabel()
        dl.addRow("Reviewer", self.ed_reviewer)
        dl.addRow("Daily target", self.sp_target)
        dl.addRow(self.pb_today)
        dl.addRow(self.lbl_stats)
        lay.addWidget(day)

        q = QGroupBox("Review queue")
        ql = QVBoxLayout(q)
        self.lbl_fields = QLabel()
        self.lbl_fields.setWordWrap(True)
        self.btn_fields = QPushButton("Add review fields to the buildings layer")
        self.btn_fields.clicked.connect(self._add_fields)
        ql.addWidget(self.lbl_fields)
        ql.addWidget(self.btn_fields)
        r = QHBoxLayout()
        self.cb_qscope = QComboBox()
        for key, label in SCOPE_LABELS:
            self.cb_qscope.addItem(label, key)
        self.btn_queue = QPushButton("Build queue")
        self.btn_queue.clicked.connect(self.build_queue)
        r.addWidget(self.cb_qscope, 1)
        r.addWidget(self.btn_queue)
        ql.addLayout(r)
        self.chk_unreviewed = QCheckBox("Skip buildings already approved or flagged")
        self.chk_unreviewed.setChecked(True)
        self.chk_unreviewed.toggled.connect(lambda v: setattr(self.review, "only_unreviewed", v))
        ql.addWidget(self.chk_unreviewed)
        self.lbl_pos = QLabel()
        ql.addWidget(self.lbl_pos)
        self.btn_review = QPushButton("▶  Start review mode")
        self.btn_review.setCheckable(True)
        self.btn_review.setStyleSheet("font-weight: bold; padding: 6px;")
        self.btn_review.toggled.connect(self._toggle_review)
        ql.addWidget(self.btn_review)
        lay.addWidget(q)

        sh = QGroupBox("Roof → base shift")
        sl = QFormLayout(sh)
        self.lbl_vector = QLabel()
        self.lbl_vector.setWordWrap(True)
        self.sp_ref = QSpinBox()
        self.sp_ref.setRange(1, 30)
        self.sp_ref.setValue(1)
        self.sp_ref.setToolTip("Floors of the building you pick the vector on. The vector is stored per floor, "
                               "so keys 1-9 shift by that many floors.")
        self.sp_ref.valueChanged.connect(lambda v: setattr(self.review, "ref_floors", v))
        btns = QHBoxLayout()
        b_pick = QPushButton("Pick vector (V)")
        b_pick.clicked.connect(self.review.start_pick)
        b_clear = QPushButton("Clear")
        b_clear.clicked.connect(self.review.clear_vectors)
        btns.addWidget(b_pick)
        btns.addWidget(b_clear)
        self.sp_nudge = QDoubleSpinBox()
        self.sp_nudge.setRange(0.01, 5)
        self.sp_nudge.setSingleStep(0.05)
        self.sp_nudge.setSuffix(" m")
        self.sp_nudge.setValue(self.settings.nudge_m)
        self.sp_nudge.valueChanged.connect(lambda v: self._set_setting("nudge_m", v))
        self.sp_nudge_big = QDoubleSpinBox()
        self.sp_nudge_big.setRange(0.05, 20)
        self.sp_nudge_big.setSuffix(" m")
        self.sp_nudge_big.setValue(self.settings.nudge_big_m)
        self.sp_nudge_big.valueChanged.connect(lambda v: self._set_setting("nudge_big_m", v))
        sl.addRow("Vector", self.lbl_vector)
        sl.addRow("Picked on a", self.sp_ref)
        sl.addRow(btns)
        sl.addRow("Nudge (WASD)", self.sp_nudge)
        sl.addRow("Shift+WASD", self.sp_nudge_big)
        lay.addWidget(sh)

        op = QGroupBox("Options")
        ol = QFormLayout(op)
        self.chk_live = QCheckBox("Check my edits instantly (overlaps, road, grid…)")
        self.chk_live.setChecked(self.settings.live_check)
        self.chk_live.toggled.connect(lambda v: self._set_setting("live_check", v))
        self.chk_zoom = QCheckBox("Zoom to each building at 1:")
        self.chk_zoom.setChecked(self.settings.auto_zoom)
        self.chk_zoom.toggled.connect(lambda v: self._set_setting("auto_zoom", v))
        self.sp_scale = QSpinBox()
        self.sp_scale.setRange(50, 100000)
        self.sp_scale.setValue(self.settings.zoom_scale)
        self.sp_scale.valueChanged.connect(lambda v: self._set_setting("zoom_scale", v))
        zr = QHBoxLayout()
        zr.addWidget(self.chk_zoom)
        zr.addWidget(self.sp_scale)
        ol.addRow(self.chk_live)
        ol.addRow(zr)
        lay.addWidget(op)

        keys = QLabel(
            "<b>Keys in review mode</b> (click the map once so it has focus)<br>"
            "<table cellspacing=3>"
            "<tr><td><b>E</b></td><td>approve + next</td><td><b>F</b></td><td>flag + next</td></tr>"
            "<tr><td><b>N</b></td><td>skip / next</td><td><b>Q</b></td><td>back</td></tr>"
            "<tr><td><b>1-9</b></td><td>shift to base, N floors</td><td><b>G</b></td><td>repeat last shift</td></tr>"
            "<tr><td><b>WASD</b></td><td>nudge (Shift = big)</td><td><b>V</b></td><td>pick roof→base</td></tr>"
            "<tr><td><b>] [</b></td><td>next/prev QC error</td><td><b>C</b></td><td>check building now</td></tr>"
            "<tr><td><b>Ctrl+Z</b></td><td>undo anything</td><td><b>Click</b></td><td>pick a building</td></tr>"
            "</table>")
        keys.setWordWrap(True)
        lay.addWidget(keys)

        b_rep = QPushButton("Export today's review report…")
        b_rep.clicked.connect(self.export_report)
        lay.addWidget(b_rep)
        lay.addStretch(1)
        return w

    def _set_setting(self, name, value):
        setattr(self.settings, name, value)
        self.settings.save()

    def _save_reviewer(self):
        self._set_setting("reviewer", self.ed_reviewer.text().strip() or "reviewer")

    def _save_target(self, v):
        self._set_setting("daily_target", int(v))
        self._update_review_panel()

    def _add_fields(self):
        lyr = self.buildings()
        if lyr is None:
            self.push("Choose a buildings layer first.", 1)
            return
        ans = QMessageBox.question(
            self, "Horus QC",
            "Add the fields %s, %s and %s to '%s'?\n\nThey store who reviewed each building and when, "
            "so your team lead can see progress." % (STATUS_FIELD, BY_FIELD, TIME_FIELD, lyr.name()))
        if ans != QMessageBox.StandardButton.Yes:
            return
        if add_review_fields(lyr):
            self.push("Review fields added.", 3)
        else:
            self.push("Couldn't add fields (the layer may be read-only). Review status will only be kept for this session.", 1)
        self._update_review_panel()

    def build_queue(self):
        lyr = self.buildings()
        if lyr is None:
            self.push("Choose a buildings layer first.", 1)
            return
        scope = self.cb_qscope.currentData()
        try:
            job = build_job(self.settings, lyr, None, self.grid(), scope, self.canvas, enabled=set())
        except ScopeError as e:
            self.push(str(e), 1)
            return
        n = self.review.set_queue(sorted(job.buildings.scope))
        self.push("Review queue: %d buildings, %d still to review." % (n, self.review.remaining()), 0, 3)
        self._update_review_panel()

    def _toggle_review(self, on):
        if on:
            if not self.review.queue:
                self.build_queue()
            self.review.activate()
        else:
            self.review.deactivate()

    def _on_tool_toggled(self, active):
        self.btn_review.blockSignals(True)
        self.btn_review.setChecked(active)
        self.btn_review.setText("■  Stop review mode" if active else "▶  Start review mode")
        self.btn_review.blockSignals(False)

    def _update_review_panel(self):
        st = self.review.stats
        target = max(1, self.settings.daily_target)
        self.pb_today.setMaximum(target)
        self.pb_today.setValue(min(target, st.reviewed))
        c = st.counts
        self.lbl_stats.setText("✔ %d approved · ⚑ %d flagged · ↘ %d shifted · 🔧 %d fixed"
                               % (c["approved"], c["flagged"], c["shifted"], c["fixed"]))
        lyr = self.buildings()
        if lyr is None:
            self.lbl_fields.setText("<i>Choose a buildings layer on the Check tab.</i>")
            self.btn_fields.setVisible(False)
        elif has_review_fields(lyr):
            self.lbl_fields.setText("✔ Review status is saved in the layer (%s, %s, %s)." % (STATUS_FIELD, BY_FIELD, TIME_FIELD))
            self.btn_fields.setVisible(False)
        else:
            self.lbl_fields.setText("Review status is only kept for this session. Add the review fields to save it in the data.")
            self.btn_fields.setVisible(True)
        if self.review.queue:
            pos = self.review.pos + 1 if self.review.pos >= 0 else 0
            self.lbl_pos.setText("Building %d of %d · %d left to review · current #%s"
                                 % (pos, len(self.review.queue), self.review.remaining(),
                                    "-" if self.review.current is None else self.review.current))
        else:
            self.lbl_pos.setText("<i>No queue yet. Choose a scope and press Build queue.</i>")
        vec, key = self.review.vector_for(self.review.current)
        where = "this grid cell" if key != "default" and key in self.review.vectors else "default"
        self.lbl_vector.setText("%s <span style='color:gray'>(%s)</span>" % (describe_vector(vec), where) if vec
                                else "<i>Not set: press V in review mode, click a roof corner, then the same corner at the base.</i>")

    def export_report(self):
        lyr = self.buildings()
        if lyr is None or not has_review_fields(lyr):
            self.push("The report needs the review fields on the buildings layer.", 1)
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export review report",
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

    # ================================================================ Settings tab
    def _build_settings_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        self.setting_widgets = {}
        groups = {}
        for attr, label, lo, hi, step, dec, group in SETTING_SPECS:
            if group not in groups:
                box = QGroupBox(group)
                groups[group] = QFormLayout(box)
                lay.addWidget(box)
            sp = QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(dec)
            sp.setValue(getattr(self.settings, attr))
            sp.valueChanged.connect(lambda v, a=attr: self._set_setting(a, float(v)))
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            groups[group].addRow(lbl, sp)
            self.setting_widgets[attr] = sp
        b = QPushButton("Reset to defaults")
        b.clicked.connect(self._reset_settings)
        lay.addWidget(b)
        about = QLabel("<p><b>Horus QC</b>: building &amp; road QC for grid-based digitising.<br>"
                       "Overlap logic from <a href='https://github.com/AmmarYasser455/ovc'>OVC</a>. "
                       "Everything runs locally; no data leaves your computer.</p>")
        about.setOpenExternalLinks(True)
        about.setWordWrap(True)
        lay.addWidget(about)
        lay.addStretch(1)
        return w

    def _reset_settings(self):
        defaults = QcSettings()
        for attr, sp in self.setting_widgets.items():
            sp.setValue(getattr(defaults, attr))

    # ================================================================ lifecycle
    def _on_read_project(self, *_):
        self.review.load_vectors()
        self._autodetect_layers()
        self._update_review_panel()

    def _on_project_cleared(self):
        self.errors = []
        self.visible_rows = []
        self.error_layer.layer_id = None
        self.hl.clear()
        self.review.queue, self.review.pos, self.review.current = [], -1, None
        self.review.vectors = {}
        self.table.setRowCount(0)
        self.lbl_summary.setText("Run the checks to see results.")
        self._update_review_panel()

    def cleanup(self):
        if self.task is not None:
            self.task.cancel()
        self.live.stop()
        self.review.deactivate()
        self.review.set_layers(None, None)
        self.hl.clear()
        self.error_layer.clear()
        project = QgsProject.instance()
        for sig, slot in ((project.cleared, self._on_project_cleared), (project.readProject, self._on_read_project)):
            try:
                sig.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
