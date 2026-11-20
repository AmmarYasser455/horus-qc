"""Review workflow state: status fields, review queue order and daily stats."""

import datetime
import json

from qgis.PyQt.QtCore import QSettings
from qgis.core import QgsFeatureRequest

from ..compat import make_field
from .geomutils import choose_metric_crs, safe_point, transformer

STATUS_FIELD = "hq_status"
BY_FIELD = "hq_by"
TIME_FIELD = "hq_time"
REVIEW_FIELDS = (STATUS_FIELD, BY_FIELD, TIME_FIELD)

APPROVED = "approved"
FLAGGED = "flagged"
SHIFTED = "shifted"
NEW = "new"


def has_review_fields(layer):
    names = set(layer.fields().names())
    return all(f in names for f in REVIEW_FIELDS)


def add_review_fields(layer):
    """Add hq_status / hq_by / hq_time to the layer. Returns True on success."""
    missing = [f for f in REVIEW_FIELDS if layer.fields().indexOf(f) < 0]
    if not missing:
        return True
    new_fields = [make_field(STATUS_FIELD, "str", 12), make_field(BY_FIELD, "str", 40), make_field(TIME_FIELD, "str", 20)]
    new_fields = [f for f in new_fields if f.name() in missing]
    if layer.isEditable():
        ok = all(layer.addAttribute(f) for f in new_fields)
    else:
        ok = layer.dataProvider().addAttributes(new_fields)
        layer.updateFields()
    return bool(ok) and has_review_fields(layer)


def now_text():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_text():
    return datetime.date.today().isoformat()


def set_status(layer, fids, status, reviewer):
    """Write the review status into the edit buffer (undoable)."""
    if not has_review_fields(layer):
        return False
    if not layer.isEditable() and not layer.startEditing():
        return False
    i_status = layer.fields().indexOf(STATUS_FIELD)
    i_by = layer.fields().indexOf(BY_FIELD)
    i_time = layer.fields().indexOf(TIME_FIELD)
    stamp = now_text()
    layer.beginEditCommand("Horus QC: mark %s" % status)
    for fid in fids:
        layer.changeAttributeValue(fid, i_status, status)
        layer.changeAttributeValue(fid, i_by, reviewer)
        layer.changeAttributeValue(fid, i_time, stamp)
    layer.endEditCommand()
    return True


def status_of(layer, fid):
    idx = layer.fields().indexOf(STATUS_FIELD)
    if idx < 0:
        return ""
    f = layer.getFeature(fid)
    if not f.isValid():
        return ""
    v = f.attribute(idx)
    return "" if v is None or str(v) == "NULL" else str(v)


def serpentine_order(layer, fids, band_m=40.0):
    """Order buildings in rows (north to south), alternating direction like mowing a lawn,
    so consecutive buildings are neighbours and the map hardly moves."""
    fids = list(fids)
    if not fids:
        return []
    req = QgsFeatureRequest().setFilterFids(fids).setNoAttributes()
    pts = {}
    for f in layer.getFeatures(req):
        if not f.hasGeometry():
            continue
        p = safe_point(f.geometry())
        if p.isNull():
            continue
        pts[f.id()] = p
    if not pts:
        return []
    metric = choose_metric_crs(layer.crs(), layer.extent())
    tx = transformer(layer.crs(), metric)
    coords = {fid: tx(p).asPoint() for fid, p in pts.items()}
    top = max(c.y() for c in coords.values())
    band_m = max(band_m, 1.0)

    def key(fid):
        c = coords[fid]
        row = int((top - c.y()) // band_m)
        return (row, c.x() if row % 2 == 0 else -c.x())

    return sorted(coords, key=key)


class DailyStats:
    """Per-day counters kept in QSettings (survives QGIS restarts)."""

    KEYS = ("approved", "flagged", "shifted", "fixed", "added")

    def __init__(self, day=None):
        self.day = day or today_text()
        self.counts = {k: 0 for k in self.KEYS}
        raw = QSettings().value("horus_qc/stats/" + self.day, "")
        if raw:
            try:
                self.counts.update({k: int(v) for k, v in json.loads(raw).items() if k in self.KEYS})
            except (ValueError, TypeError):
                pass

    def bump(self, key, n=1):
        if self.day != today_text():  # midnight rollover
            self.__init__()
        self.counts[key] = self.counts.get(key, 0) + n
        QSettings().setValue("horus_qc/stats/" + self.day, json.dumps(self.counts))

    @property
    def reviewed(self):
        return self.counts["approved"] + self.counts["flagged"]
