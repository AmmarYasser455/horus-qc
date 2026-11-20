"""Synthetic test layers in EPSG:32636 (UTM 36N, Egypt). Each cluster triggers one check."""

from qgis.core import QgsFeature, QgsGeometry, QgsProject, QgsVectorLayer


def sq(x, y, w, h=None):
    h = w if h is None else h
    return "POLYGON((%s %s, %s %s, %s %s, %s %s, %s %s))" % (x, y, x + w, y, x + w, y + h, x, y + h, x, y)


BUILDINGS = {
    "clean": sq(-200, 0, 10),
    "dup_a": sq(0, 0, 10),
    "dup_b": sq(0, 0, 10),
    "wall_a": sq(100, 0, 10),
    "wall_b": "POLYGON((109.8 0, 120 0, 120 10, 109.8 10, 109.8 0))",
    "ovl_a": sq(200, 0, 10),
    "ovl_b": sq(205, 0, 10),
    "outer": sq(300, 0, 20),
    "inner": sq(305, 5, 3),
    "gap_a": sq(400, 0, 10),
    "gap_b": "POLYGON((410.3 0, 420 0, 420 10, 410.3 10, 410.3 0))",
    "bowtie": "POLYGON((500 0, 510 10, 510 0, 500 10, 500 0))",
    "tiny": sq(600, 0, 2),
    "multi": "MULTIPOLYGON(((700 0, 710 0, 710 10, 700 10, 700 0)),((720 0, 725 0, 725 5, 720 5, 720 0)))",
    "wobbly": "POLYGON((800 0, 810 1, 810 10, 800 10, 800 0))",
    "spike": "POLYGON((900 0, 910 0, 910 10, 905.3 10, 905 25, 904.7 10, 900 10, 900 0))",
    "onroad": sq(1000, 0, 10),
    "edge": sq(945, 20, 10),
}

ROADS = {
    "through": "LINESTRING(990 5, 1020 5)",
    "main": "LINESTRING(0 200, 100 200)",
    "main_dup": "LINESTRING(0 200, 100 200)",
    "t_junction": "LINESTRING(50 200, 50 250)",
    "undershoot": "LINESTRING(70 201.5, 70 230)",
    "overshoot": "LINESTRING(30 202, 30 180)",
    "island": "LINESTRING(500 500, 510 500)",
    "short": "LINESTRING(200 300, 200.5 300)",
    "loop": "LINESTRING(300 300, 310 310, 310 300, 300 310)",
}

GRID = {"cell": "POLYGON((-250 -50, 950 -50, 950 50, -250 50, -250 -50))"}


def make_layer(kind, wkts, crs="EPSG:32636", name=None):
    geom_type = {"building": "MultiPolygon", "road": "LineString", "grid": "Polygon"}[kind]
    lyr = QgsVectorLayer("%s?crs=%s&field=name:string(20)" % (geom_type, crs), name or kind, "memory")
    feats = []
    for label, wkt in wkts.items():
        f = QgsFeature(lyr.fields())
        g = QgsGeometry.fromWkt(wkt)
        if geom_type.startswith("Multi"):
            g.convertToMultiType()
        f.setGeometry(g)
        f["name"] = label
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    lyr.updateExtents()
    QgsProject.instance().addMapLayer(lyr)
    return lyr


def fid_of(layer, label):
    for f in layer.getFeatures():
        if f["name"] == label:
            return f.id()
    raise KeyError(label)
