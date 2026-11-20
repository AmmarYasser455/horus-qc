import unittest

from qgis.core import QgsCoordinateReferenceSystem, QgsProject

from horus_qc.core.catalog import CHECKS
from horus_qc.core.engine import run_job
from horus_qc.core.jobs import SCOPE_ALL, SCOPE_GRID_SELECTED, ScopeError, build_job, live_check
from horus_qc.core.settings import QcSettings
from tests.fixtures import BUILDINGS, GRID, ROADS, fid_of, make_layer


def settings():
    s = QcSettings()
    s.enabled_checks = set(CHECKS)  # everything, including off-by-default checks
    return s


def by_check(errors):
    out = {}
    for e in errors:
        out.setdefault(e.check, []).append(e)
    return out


class EngineTest(unittest.TestCase):
    def setUp(self):
        QgsProject.instance().removeAllMapLayers()
        self.b = make_layer("building", BUILDINGS)
        self.r = make_layer("road", ROADS)
        self.g = make_layer("grid", GRID)

    def run_all(self, s=None, buildings=True, roads=True, grid=True, scope=SCOPE_ALL):
        job = build_job(s or settings(), self.b if buildings else None, self.r if roads else None,
                        self.g if grid else None, scope)
        return by_check(run_job(job))

    def names(self, errors, layer):
        return sorted({layer.getFeature(e.fid)["name"] for e in errors})

    def test_building_checks(self):
        e = self.run_all()
        n = lambda k: self.names(e.get(k, []), self.b)  # noqa: E731
        self.assertEqual(n("b_duplicate"), ["dup_b"])
        self.assertEqual(n("b_shared_wall"), ["wall_a"])  # reported on the smaller building
        self.assertEqual(n("b_overlap"), ["ovl_a"])
        self.assertEqual(n("b_contained"), ["inner"])
        self.assertEqual(n("b_gap"), ["gap_b"])
        self.assertEqual(n("b_self_intersection"), ["bowtie"])
        self.assertEqual(n("b_tiny"), ["tiny"])
        self.assertEqual(n("b_multipart"), ["multi"])
        self.assertEqual(n("b_not_square"), ["wobbly"])
        self.assertEqual(n("b_spike"), ["spike"])
        self.assertEqual(n("b_on_road"), ["onroad"])
        self.assertEqual(n("b_crosses_grid"), ["edge"])
        self.assertEqual(n("b_outside_grid"), ["onroad"])
        self.assertNotIn("clean", sum((n(k) for k in e if k.startswith("b_")), []))

    def test_road_checks(self):
        e = self.run_all(buildings=False)
        n = lambda k: self.names(e.get(k, []), self.r)  # noqa: E731
        self.assertEqual(n("r_duplicate"), ["main_dup"])
        self.assertEqual(n("r_undershoot"), ["undershoot"])
        self.assertEqual(n("r_overshoot"), ["overshoot"])
        self.assertIn("island", n("r_isolated"))
        self.assertEqual(n("r_short"), ["short"])
        self.assertEqual(n("r_self_intersection"), ["loop"])
        dead = n("r_dead_end")
        self.assertIn("t_junction", dead)  # its far end, not the T-junction end
        t_errs = [x for x in e["r_dead_end"] if x.fid == fid_of(self.r, "t_junction")]
        self.assertEqual(len(t_errs), 1, "T-junction end must count as connected")
        self.assertNotIn("main", dead)

    def test_geographic_crs_gives_same_results(self):
        proj = self.run_all(roads=False, grid=False)
        wgs = QgsCoordinateReferenceSystem("EPSG:4326")
        from qgis.core import QgsCoordinateTransform, QgsFeature, QgsVectorLayer

        ct = QgsCoordinateTransform(self.b.crs(), wgs, QgsProject.instance())
        lyr = QgsVectorLayer("MultiPolygon?crs=EPSG:4326&field=name:string(20)", "b4326", "memory")
        feats = []
        for f in self.b.getFeatures():
            g = f.geometry()
            g.transform(ct)
            nf = QgsFeature(lyr.fields())
            nf.setGeometry(g)
            nf["name"] = f["name"]
            feats.append(nf)
        lyr.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(lyr)
        geo = by_check(run_job(build_job(settings(), lyr, None, None, SCOPE_ALL)))
        self.assertEqual({k: len(v) for k, v in proj.items()}, {k: len(v) for k, v in geo.items()})

    def test_grid_scope_requires_selection(self):
        with self.assertRaises(ScopeError):
            build_job(settings(), self.b, None, self.g, SCOPE_GRID_SELECTED)
        self.g.selectAll()
        e = by_check(run_job(build_job(settings(), self.b, self.r, self.g, SCOPE_GRID_SELECTED)))
        self.assertNotIn("onroad", self.names(e.get("b_on_road", []), self.b))  # outside the cell

    def test_live_check(self):
        self.b.startEditing()
        fid = fid_of(self.b, "clean")
        g = self.b.getFeature(fid).geometry()
        g.translate(195, 0)  # now overlaps dup_a / dup_b
        self.b.changeGeometry(fid, g)
        errs = live_check(settings(), self.b, self.r, self.g, building_fids={fid})
        self.assertTrue(any(x.check in ("b_overlap", "b_duplicate", "b_contained") for x in errs), [x.check for x in errs])
        self.b.rollBack()

    def test_ignored_errors_are_marked(self):
        e = self.run_all()
        key = e["b_tiny"][0].key
        job = build_job(settings(), self.b, None, None, SCOPE_ALL, ignored={key})
        tiny = [x for x in run_job(job) if x.check == "b_tiny"]
        self.assertEqual(tiny[0].status, "ignored")


if __name__ == "__main__":
    unittest.main()
