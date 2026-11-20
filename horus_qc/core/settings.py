"""User-tunable thresholds, persisted in QSettings under ``horus_qc/``."""

import getpass
from dataclasses import dataclass, field, fields

from qgis.PyQt.QtCore import QSettings

from .catalog import CHECKS, DEFAULT_ENABLED

PREFIX = "horus_qc/"


def _default_reviewer():
    try:
        return getpass.getuser()
    except Exception:
        return "reviewer"


@dataclass
class QcSettings:
    # building overlaps
    min_overlap_m2: float = 0.05
    duplicate_ratio: float = 0.95
    contained_ratio: float = 0.95
    partial_ratio: float = 0.15
    gap_tolerance_m: float = 0.5
    # building shape
    min_area_m2: float = 6.0
    max_area_m2: float = 5000.0
    square_min_dev_deg: float = 3.0
    square_max_dev_deg: float = 12.0
    spike_angle_deg: float = 15.0
    # building vs road / grid
    road_buffer_m: float = 1.0
    road_min_overlap_m2: float = 0.5
    road_setback_m: float = 3.0
    grid_edge_m: float = 5.0
    # roads
    connect_tolerance_m: float = 0.05
    undershoot_m: float = 3.0
    overshoot_m: float = 3.0
    min_road_length_m: float = 1.0
    road_overlap_min_m: float = 1.0
    isolated_max_length_m: float = 300.0
    # review workflow
    reviewer: str = field(default_factory=_default_reviewer)
    daily_target: int = 600
    nudge_m: float = 0.25
    nudge_big_m: float = 1.0
    auto_zoom: bool = True
    zoom_scale: int = 400
    live_check: bool = True
    serpentine_band_m: float = 40.0
    enabled_checks: set = field(default_factory=lambda: set(DEFAULT_ENABLED))

    @classmethod
    def load(cls):
        s = QSettings()
        obj = cls()
        for f in fields(cls):
            key = PREFIX + f.name
            if not s.contains(key):
                continue
            raw = s.value(key)
            try:
                if f.name == "enabled_checks":
                    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(",")
                    obj.enabled_checks = {k for k in items if k in CHECKS}
                elif f.type in (bool, "bool"):
                    obj.__setattr__(f.name, str(raw).lower() in ("1", "true", "yes"))
                elif f.type in (int, "int"):
                    obj.__setattr__(f.name, int(float(raw)))
                elif f.type in (float, "float"):
                    obj.__setattr__(f.name, float(raw))
                else:
                    obj.__setattr__(f.name, str(raw))
            except (TypeError, ValueError):
                pass
        return obj

    def save(self):
        s = QSettings()
        for f in fields(self):
            val = getattr(self, f.name)
            if f.name == "enabled_checks":
                val = ",".join(sorted(val))
            s.setValue(PREFIX + f.name, val)


# (attribute, label, min, max, step, decimals, group) for the Settings tab
SETTING_SPECS = [
    ("min_overlap_m2", "Ignore overlaps smaller than (m²)", 0.0, 10.0, 0.05, 2, "Building overlaps"),
    ("duplicate_ratio", "Duplicate if overlap ≥ (share of larger)", 0.5, 1.0, 0.01, 2, "Building overlaps"),
    ("contained_ratio", "Inside another if overlap ≥ (share of smaller)", 0.5, 1.0, 0.01, 2, "Building overlaps"),
    ("partial_ratio", "Real overlap if ≥ (share of smaller), else shared wall", 0.01, 0.9, 0.01, 2, "Building overlaps"),
    ("gap_tolerance_m", "Hairline gap tolerance (m)", 0.0, 5.0, 0.05, 2, "Building overlaps"),
    ("min_area_m2", "Minimum building area (m²)", 0.0, 500.0, 0.5, 1, "Building shape"),
    ("max_area_m2", "Maximum building area (m²)", 10.0, 1e6, 100.0, 0, "Building shape"),
    ("square_min_dev_deg", "Flag corners off 90° by more than (°)", 0.5, 30.0, 0.5, 1, "Building shape"),
    ("square_max_dev_deg", "…but less than (°), larger is intentional", 2.0, 45.0, 0.5, 1, "Building shape"),
    ("spike_angle_deg", "Spike if corner sharper than (°)", 1.0, 45.0, 1.0, 0, "Building shape"),
    ("road_buffer_m", "Road half-width buffer (m)", 0.0, 30.0, 0.25, 2, "Buildings vs roads & grids"),
    ("road_min_overlap_m2", "Min building-on-road area (m²)", 0.0, 50.0, 0.1, 2, "Buildings vs roads & grids"),
    ("road_setback_m", "Set-back distance from road (m)", 0.0, 50.0, 0.5, 1, "Buildings vs roads & grids"),
    ("grid_edge_m", "Ignore road ends within (m) of grid edge", 0.0, 100.0, 1.0, 1, "Buildings vs roads & grids"),
    ("connect_tolerance_m", "Roads connected if closer than (m)", 0.0, 2.0, 0.01, 2, "Roads"),
    ("undershoot_m", "Undershoot search distance (m)", 0.0, 50.0, 0.5, 1, "Roads"),
    ("overshoot_m", "Overshoot max tail length (m)", 0.0, 50.0, 0.5, 1, "Roads"),
    ("min_road_length_m", "Minimum road segment length (m)", 0.0, 50.0, 0.5, 1, "Roads"),
    ("road_overlap_min_m", "Flag shared road length ≥ (m)", 0.0, 50.0, 0.5, 1, "Roads"),
    ("isolated_max_length_m", "Isolated network piece if total ≤ (m)", 0.0, 1e5, 50.0, 0, "Roads"),
]
