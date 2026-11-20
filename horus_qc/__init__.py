"""Horus QC — building & road quality control for grid-based digitising in QGIS."""


def classFactory(iface):  # noqa: N802 (QGIS plugin entry point)
    from .plugin import HorusQcPlugin

    return HorusQcPlugin(iface)
