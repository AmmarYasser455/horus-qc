"""Smoke-test the real plugin UI headless: dock, background task, fixes, review keys, shift, live check."""

import os
import tempfile
import time
import unittest

from qgis.PyQt.QtCore import QCoreApplication, QEvent, Qt
from qgis.PyQt.QtGui import QKeyEvent
from qgis.core import QgsCoordinateReferenceSystem, QgsPointXY, QgsProject, QgsRectangle

from horus_qc.core.jobs import SCOPE_ALL
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

    def run_checks(self):
        d = self.dock
        d.cb_scope.setCurrentIndex(d.cb_scope.findData(SCOPE_ALL))
        d.run_checks()
        self.assertTrue(wait_for(lambda: d.task is None), "task did not finish")
        return d.errors

    def test_autodetect_and_run(self):
        d = self.dock
        self.assertIs(d.buildings(), self.b)
        self.assertIs(d.roads(), self.r)
        self.assertIs(d.grid(), self.g)
        errors = self.run_checks()
        self.assertGreater(len(errors), 20)
        lyr = d.error_layer.layer()
        self.assertIsNotNone(lyr)
        self.assertEqual(lyr.featureCount(), sum(1 for e in errors if not e.geometry.isNull()))
        self.assertEqual(d.table.rowCount(), len(errors))
        # severity filter
        d.cb_sev.setCurrentIndex(d.cb_sev.findData("critical"))
        self.assertTrue(all(d.errors[i].severity == "critical" for i in d.visible_rows))
        d.cb_sev.setCurrentIndex(0)

    def test_fix_from_table_and_export(self):
        d = self.dock
        errors = self.run_checks()
        idx = next(i for i, e in enumerate(errors) if e.check == "b_duplicate")
        row = d.visible_rows.index(idx)
        d.table.selectRow(row)
        self.assertTrue(d.btn_fix.isEnabled())
        self.assertIn("Delete", d.guide.toPlainText() + d.btn_fix.toolTip())
        n_before = d.error_layer.layer().featureCount()
        d.fix_selected()
        self.assertEqual(errors[idx].status, "fixed")
        self.assertEqual(d.error_layer.layer().featureCount(), n_before - 1)
        self.assertEqual(self.b.getFeature(errors[idx].fid).isValid(), False)  # duplicate deleted in edit buffer
        # navigation
        d.step_error(1)
        d.step_error(-1)
        out = tempfile.mkdtemp()
        d._export_csv(os.path.join(out, "e.csv"))
        d._export_gpkg(os.path.join(out, "e.gpkg"))
        self.assertTrue(os.path.getsize(os.path.join(out, "e.csv")) > 100)
        self.assertTrue(os.path.exists(os.path.join(out, "e.gpkg")))

    def test_ignore_persists_in_project(self):
        d = self.dock
        self.run_checks()
        d.table.selectRow(0)
        _i, err = d.selected_error()
        d.ignore_selected()
        self.assertIn(err.key, d.ignored_keys())
        errors = self.run_checks()
        self.assertEqual([e.status for e in errors if e.key == err.key], ["ignored"])

    def test_review_mode_keys_shift_and_approve(self):
        d = self.dock
        rc = d.review
        self.assertTrue(add_review_fields(self.b))
        d._update_review_panel()
        d.cb_qscope.setCurrentIndex(d.cb_qscope.findData(SCOPE_ALL))
        d.build_queue()
        self.assertEqual(len(rc.queue), len(BUILDINGS))
        d.btn_review.setChecked(True)
        self.assertIs(self.iface.mapCanvas().mapTool(), rc.tool)
        self.assertIsNotNone(rc.current)
        # pick a 1.5 m roof->base vector (south-west lean) on a 1-floor building
        rc.start_pick()
        rc.on_click(QgsPointXY(100, 100))
        rc.on_click(QgsPointXY(99, 98.5))
        self.assertAlmostEqual(rc.vectors["default"][0], -1.0, places=6)
        self.assertAlmostEqual(rc.vectors["default"][1], -1.5, places=6)
        fid = rc.current
        before = self.b.getFeature(fid).geometry().centroid().asPoint()
        # real key event through the map tool: "2" = shift by 2 floors
        rc.tool.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_2, Qt.KeyboardModifier.NoModifier, "2"))
        after = self.b.getFeature(fid).geometry().centroid().asPoint()
        self.assertAlmostEqual(after.x() - before.x(), -2.0, places=4)
        self.assertAlmostEqual(after.y() - before.y(), -3.0, places=4)
        # nudge east with D
        rc.tool.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_D, Qt.KeyboardModifier.NoModifier, "d"))
        after2 = self.b.getFeature(fid).geometry().centroid().asPoint()
        self.assertAlmostEqual(after2.x() - after.x(), 0.25, places=4)
        # approve -> status written and moves on
        rc.tool.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e"))
        self.assertEqual(self.b.getFeature(fid)[STATUS_FIELD], APPROVED)
        self.assertNotEqual(rc.current, fid)
        self.assertEqual(rc.remaining(), len(BUILDINGS) - 1)
        # Ctrl+Z still belongs to QGIS
        self.assertFalse(rc.on_key(Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier))
        # undo works for the shift
        self.assertTrue(self.b.undoStack().count() >= 3)
        d.btn_review.setChecked(False)
        self.assertIsNot(self.iface.mapCanvas().mapTool(), rc.tool)

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
