"""The ⚙ Settings window: every option and threshold, out of the way of the main panel."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.catalog import BUILDING_CHECKS, ROAD_CHECKS
from ..core.review import has_review_fields
from ..core.settings import SETTING_SPECS, QcSettings


class SettingsDialog(QDialog):
    def __init__(self, dock):
        super().__init__(dock)
        self.dock = dock
        self.s = dock.settings
        self.setWindowTitle("Horus QC settings")
        self.resize(460, 640)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        lay = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll)

        # ---------------------------------------------------------- review
        rv = QGroupBox("Review")
        f = QFormLayout(rv)
        self.ed_reviewer = QLineEdit(self.s.reviewer)
        self.ed_reviewer.editingFinished.connect(
            lambda: self._set("reviewer", self.ed_reviewer.text().strip() or "reviewer"))
        f.addRow("Your name", self.ed_reviewer)
        self.sp_target = self._int(self.s.daily_target, 1, 100000, lambda v: self._set("daily_target", v))
        f.addRow("Daily target", self.sp_target)
        self.chk_skip = QCheckBox("Skip buildings already approved or flagged")
        self.chk_skip.setChecked(dock.review.only_unreviewed)
        self.chk_skip.toggled.connect(lambda v: setattr(dock.review, "only_unreviewed", v))
        f.addRow(self.chk_skip)
        self.sp_ref = self._int(dock.review.ref_floors, 1, 30, lambda v: setattr(dock.review, "ref_floors", v))
        self.sp_ref.setToolTip("Floors of the building you pick the lean on (V). The lean is stored per floor.")
        f.addRow("Pick the lean on a building of (floors)", self.sp_ref)
        self.sp_nudge = self._dbl(self.s.nudge_m, 0.01, 5, 0.05, " m", lambda v: self._set("nudge_m", v))
        f.addRow("W A S D step", self.sp_nudge)
        self.sp_big = self._dbl(self.s.nudge_big_m, 0.05, 20, 0.25, " m", lambda v: self._set("nudge_big_m", v))
        f.addRow("Shift + W A S D step", self.sp_big)
        zr = QHBoxLayout()
        self.chk_zoom = QCheckBox("Zoom to each building at 1:")
        self.chk_zoom.setChecked(self.s.auto_zoom)
        self.chk_zoom.toggled.connect(lambda v: self._set("auto_zoom", v))
        self.sp_scale = self._int(self.s.zoom_scale, 50, 100000, lambda v: self._set("zoom_scale", v))
        zr.addWidget(self.chk_zoom)
        zr.addWidget(self.sp_scale)
        f.addRow(zr)
        self.chk_live = QCheckBox("Warn me instantly when an edit creates a problem")
        self.chk_live.setChecked(self.s.live_check)
        self.chk_live.toggled.connect(lambda v: self._set("live_check", v))
        f.addRow(self.chk_live)
        self.lbl_fields = QLabel()
        self.lbl_fields.setWordWrap(True)
        self.btn_fields = QPushButton("Save review status in the buildings layer")
        self.btn_fields.clicked.connect(self._add_fields)
        f.addRow(self.lbl_fields)
        f.addRow(self.btn_fields)
        lay.addWidget(rv)
        self._refresh_fields()

        # ---------------------------------------------------------- checks
        ck = QGroupBox("Which problems to look for")
        cl = QVBoxLayout(ck)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumHeight(220)
        from .dock import severity_icon

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
                it.setCheckState(0, Qt.CheckState.Checked if d.key in self.s.enabled_checks else Qt.CheckState.Unchecked)
                top.addChild(it)
            top.setExpanded(True)
        self.tree.itemChanged.connect(self._check_toggled)
        cl.addWidget(self.tree)
        lay.addWidget(ck)

        # ---------------------------------------------------------- thresholds
        self.spins = {}
        groups = {}
        for attr, label, lo, hi, step, dec, group in SETTING_SPECS:
            if group not in groups:
                box = QGroupBox(group)
                box.setCheckable(False)
                groups[group] = QFormLayout(box)
                lay.addWidget(box)
            sp = self._dbl(getattr(self.s, attr), lo, hi, step, "", lambda v, a=attr: self._set(a, float(v)), dec)
            lbl = QLabel(label)
            lbl.setWordWrap(True)
            groups[group].addRow(lbl, sp)
            self.spins[attr] = sp
        lay.addStretch(1)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        reset = bb.addButton("Reset to defaults", QDialogButtonBox.ButtonRole.ResetRole)
        reset.clicked.connect(self._reset)
        bb.rejected.connect(self.close)
        bb.accepted.connect(self.close)
        outer.addWidget(bb)

    # ---------------------------------------------------------- helpers
    def _int(self, value, lo, hi, cb):
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setValue(int(value))
        sp.valueChanged.connect(cb)
        return sp

    def _dbl(self, value, lo, hi, step, suffix, cb, decimals=2):
        sp = QDoubleSpinBox()
        sp.setRange(lo, hi)
        sp.setSingleStep(step)
        sp.setDecimals(decimals)
        sp.setSuffix(suffix)
        sp.setValue(float(value))
        sp.valueChanged.connect(cb)
        return sp

    def _set(self, name, value):
        setattr(self.s, name, value)
        self.s.save()
        self.dock.refresh()

    def _check_toggled(self, item, _col):
        key = item.data(0, Qt.ItemDataRole.UserRole)
        if not key:
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.s.enabled_checks.add(key)
        else:
            self.s.enabled_checks.discard(key)
        self.s.save()

    def _refresh_fields(self):
        lyr = self.dock.buildings()
        if lyr is not None and has_review_fields(lyr):
            self.lbl_fields.setText("✔ Review status is saved in the buildings layer (hq_status, hq_by, hq_time).")
            self.btn_fields.setVisible(False)
        else:
            self.lbl_fields.setText("Review status is only kept until QGIS closes.")
            self.btn_fields.setVisible(lyr is not None)

    def _add_fields(self):
        self.dock.add_review_fields(ask=True)
        self._refresh_fields()

    def _reset(self):
        d = QcSettings()
        for attr, sp in self.spins.items():
            sp.setValue(getattr(d, attr))
        self.sp_nudge.setValue(d.nudge_m)
        self.sp_big.setValue(d.nudge_big_m)
        self.sp_scale.setValue(d.zoom_scale)
        self.chk_zoom.setChecked(d.auto_zoom)
        self.chk_live.setChecked(d.live_check)
        self.sp_target.setValue(d.daily_target)
