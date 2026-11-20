"""Smoke-test the real plugin UI headless: panel, background check, list, review keys, settings, live check."""

import os
import tempfile
import time
import unittest

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsProject, QgsRectangle

from horus_qc.core.jobs import SCOPE_ALL, SCOPE_EXTENT, SCOPE_GRID_SELECTED
from horus_qc.core.review import APPROVED, STATUS_FIELD, add_review_fields
from tests.fixtures import BUILDINGS, GRID, ROADS, fid_of, make_layer


def wait_for(cond, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.01)
    return False


def key(tool, k, text):
    tool.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, k, Qt.KeyboardModifier.NoModifier, text))


class UiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from qgis.testing.mocked import get_iface

        cls.iface = get_iface()

    def setUp(self):
        QgsProject.instance().removeAllMapLayers()
        self.b = make_layer("building", BUILDINGS, name="buildings")
        self.r = make_layer("road", ROADS, name="roads")
        self.g = make_layer("grid", GRID, name="grid zones")
        canvas = self.iface.mapCanvas()
        canvas.setDestinationCrs(QgsCoordinateReferenceSystem("EPSG:32636"))
        canvas.setExtent(QgsRectangle(-300, -100, 1100, 600))
        from horus_qc.plugin import HorusQcPlugin

        self.plugin = HorusQcPlugin(self.iface)
        self.plugin.initGui()
        self.plugin.toggle_panel(True)
        self.dock = self.plugin.dock

    def tearDown(self):
        self.plugin.unload()
        for lyr in (self.b, self.r):
            if lyr.isEditable():
                lyr.rollBack()

    def set_area(self, scope):
        self.dock.cb_area.setCurrentIndex(self.dock.cb_area.findData(scope))

    def run_checks(self):
        d = self.dock
        d.run_checks()
        self.assertTrue(wait_for(lambda: d.task is None), "task did not finish")
        return d.errors

    def test_no_fix_buttons(self):
        self.assertFalse(hasattr(self.dock, "btn_fix"))
        with self.assertRaises(ImportError):
            __import__("horus_qc.core.fixes")

    def test_autodetect_and_auto_area(self):
        d = self.dock
        self.assertIs(d.buildings(), self.b)
        self.assertIs(d.roads(), self.r)
        self.assertIs(d.grid(), self.g)
        self.assertEqual(d.current_scope(), SCOPE_EXTENT)  # nothing selected in the grid
        self.assertIn("map", d.lbl_area.text())
        self.g.selectAll()
        self.assertEqual(d.current_scope(), SCOPE_GRID_SELECTED)
        self.assertIn("1 selected grid cell", d.lbl_area.text())

    def test_check_groups_navigation_ignore_export(self):
        d = self.dock
        self.set_area(SCOPE_ALL)
        errors = self.run_checks()
        self.assertGreater(len(errors), 20)
        # one group per problem type, children = problems
        self.assertEqual(d.tree.topLevelItemCount(), len({e.check for e in errors if e.status == "open"}))
        self.assertEqual(len(d.order), sum(1 for e in errors if e.status == "open"))
        self.assertEqual(d.error_layer.layer().featureCount(), sum(1 for e in errors if not e.geometry.isNull()))
        # first problem is selected, with guidance and no fix wording
        i0, e0 = d.selected_error()
        self.assertEqual(i0, d.order[0])
        self.assertIn("What to do", d.guide.toPlainText())
        self.assertNotIn("fix", d.guide.toPlainText().lower())
        d.step_error(1)
        self.assertEqual(d.selected_error()[0], d.order[1])
        d.step_error(-1)
        # ignore removes it from the list and remembers it
        n = len(d.order)
        d.ignore_selected()
        self.assertEqual(len(d.order), n - 1)
        self.assertIn(e0.key, d.ignored_keys())
        errors = self.run_checks()
        self.assertNotIn(e0.key, [errors[i].key for i in d.order])
        out = tempfile.mkdtemp()
        d._export_csv(os.path.join(out, "e.csv"))
        d._export_gpkg(os.path.join(out, "e.gpkg"))
        self.assertTrue(os.path.getsize(os.path.join(out, "e.csv")) > 100)
        self.assertTrue(os.path.exists(os.path.join(out, "e.gpkg")))

    def test_review_keys_shift_and_approve(self):
        d = self.dock
        rc = d.review
        self.assertTrue(add_review_fields(self.b))
        self.set_area(SCOPE_ALL)
        self.assertTrue(d.start_review(ask_fields=False))
        self.assertEqual(len(rc.queue), len(BUILDINGS))
        self.assertIs(self.iface.mapCanvas().mapTool(), rc.tool)
        self.assertIsNotNone(rc.current)
        # pick a 1.5 m roof->base lean on a 1-floor building
        rc.start_pick()
        rc.on_click(QgsPointXY(100, 100))
        rc.on_click(QgsPointXY(99, 98.5))
        self.assertIn("SW", d.lbl_vector.text())
        fid = rc.current
        before = self.b.getFeature(fid).geometry().centroid().asPoint()
        key(rc.tool, Qt.Key.Key_2, "2")
        after = self.b.getFeature(fid).geometry().centroid().asPoint()
        self.assertAlmostEqual(after.x() - before.x(), -2.0, places=4)
        self.assertAlmostEqual(after.y() - before.y(), -3.0, places=4)
        key(rc.tool, Qt.Key.Key_D, "d")
        after2 = self.b.getFeature(fid).geometry().centroid().asPoint()
        self.assertAlmostEqual(after2.x() - after.x(), 0.25, places=4)
        done_before = d.pb_today.value()
        key(rc.tool, Qt.Key.Key_E, "e")
        self.assertEqual(self.b.getFeature(fid)[STATUS_FIELD], APPROVED)
        self.assertNotEqual(rc.current, fid)
        self.assertEqual(rc.remaining(), len(BUILDINGS) - 1)
        self.assertEqual(d.pb_today.value(), min(done_before + 1, d.pb_today.maximum()))
        self.assertFalse(rc.on_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier))
        # the highlight follows undo
        rc._set_current(fid)
        self.b.undoStack().undo()  # status
        self.b.undoStack().undo()  # nudge
        band = rc.hl.current_bands[0].asGeometry()
        self.assertAlmostEqual(band.centroid().asPoint().x(), after.x(), places=3)
        d.btn_review.setChecked(False)
        self.assertIsNot(self.iface.mapCanvas().mapTool(), rc.tool)
        self.assertIn("Start", d.btn_review.text())

    def test_settings_dialog_applies_changes(self):
        from horus_qc.ui.settings_dialog import SettingsDialog

        dlg = SettingsDialog(self.dock)
        dlg.spins["min_area_m2"].setValue(12.5)
        self.assertEqual(self.dock.settings.min_area_m2, 12.5)
        dlg.sp_target.setValue(450)
        self.assertEqual(self.dock.pb_today.maximum(), 450)
        top = dlg.tree.topLevelItem(0)
        first = top.child(0)
        key0 = first.data(0, Qt.ItemDataRole.UserRole)
        first.setCheckState(0, Qt.CheckState.Unchecked)
        self.assertNotIn(key0, self.dock.settings.enabled_checks)
        dlg._reset()
        self.assertEqual(self.dock.settings.min_area_m2, 6.0)
        first.setCheckState(0, Qt.CheckState.Checked)
        dlg.close()

    def test_live_check_after_edit(self):
        d = self.dock
        found = []
        d.live.found.connect(lambda errs: found.append(errs))
        self.b.startEditing()
        fid = fid_of(self.b, "clean")
        g = self.b.getFeature(fid).geometry()
        g.translate(1201, 5)  # lands on the road at x=1000
        self.b.changeGeometry(fid, g)
        self.assertTrue(wait_for(lambda: bool(found), 5), "live check did not fire")
        self.assertIn("b_on_road", [e.check for e in found[-1]])


if __name__ == "__main__":
    unittest.main()
