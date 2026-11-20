"""QGIS plugin entry: toolbar, menu and the Horus QC dock."""

import os

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon

from .compat import QAction

MENU = "&Horus QC"


class HorusQcPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.dock = None
        self.toolbar = None
        self.actions = []

    def icon(self, name):
        return QIcon(os.path.join(self.plugin_dir, "icons", name))

    # ------------------------------------------------------------------ QGIS hooks
    def initGui(self):  # noqa: N802
        self.toolbar = self.iface.addToolBar("Horus QC")
        self.toolbar.setObjectName("HorusQcToolbar")

        self.act_panel = QAction(self.icon("horus.svg"), "Horus QC panel", self.iface.mainWindow())
        self.act_panel.setCheckable(True)
        self.act_panel.setToolTip("Show or hide the Horus QC panel")
        self.act_panel.toggled.connect(self.toggle_panel)

        self.act_run = QAction(self.icon("run.svg"), "Run Horus QC checks", self.iface.mainWindow())
        self.act_run.triggered.connect(self.run_checks)

        self.act_review = QAction(self.icon("review.svg"), "Horus review mode", self.iface.mainWindow())
        self.act_review.setCheckable(True)
        self.act_review.setToolTip("Review mode: keys E/F/N/Q, 1-9 shift to base, WASD nudge")
        self.act_review.toggled.connect(self.toggle_review)

        for act, shortcut in ((self.act_panel, "Ctrl+Alt+H"), (self.act_run, ""), (self.act_review, "")):
            self.toolbar.addAction(act)
            self.iface.addPluginToVectorMenu(MENU, act)
            try:
                self.iface.registerMainWindowAction(act, shortcut)
            except Exception:
                pass
            self.actions.append(act)

    def unload(self):
        for act in self.actions:
            self.iface.removePluginVectorMenu(MENU, act)
            try:
                self.iface.unregisterMainWindowAction(act)
            except Exception:
                pass
        self.actions = []
        if self.dock is not None:
            self.dock.cleanup()
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.toolbar is not None:
            self.toolbar.deleteLater()
            self.toolbar = None

    # ------------------------------------------------------------------ actions
    def ensure_dock(self):
        if self.dock is None:
            from .ui.dock import HorusDock

            self.dock = HorusDock(self.iface, self.plugin_dir)
            self.iface.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.dock)
            self.dock.visibilityChanged.connect(self._dock_visibility)
            self.dock.review.tool_toggled.connect(self._review_state)
        return self.dock

    def toggle_panel(self, show):
        dock = self.ensure_dock()
        dock.setVisible(show)
        if show:
            dock.raise_()

    def _dock_visibility(self, visible):
        self.act_panel.blockSignals(True)
        self.act_panel.setChecked(visible)
        self.act_panel.blockSignals(False)

    def run_checks(self):
        dock = self.ensure_dock()
        if not dock.isVisible():
            self.act_panel.setChecked(True)
        dock.run_checks()

    def toggle_review(self, on):
        dock = self.ensure_dock()
        if on and not dock.isVisible():
            self.act_panel.setChecked(True)
        dock.btn_review.setChecked(on)

    def _review_state(self, active):
        self.act_review.blockSignals(True)
        self.act_review.setChecked(active)
        self.act_review.blockSignals(False)
