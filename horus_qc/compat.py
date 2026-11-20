"""Compatibility helpers so one code base runs on QGIS 3 (Qt5) and QGIS 4 (Qt6).

Rule of thumb used across the plugin: always use fully scoped Qt enums
(``Qt.ItemDataRole.UserRole``), which work on PyQt5 >= 5.11 and PyQt6, and
resolve QGIS enums that moved between versions here, once.
"""

from qgis.PyQt import QtCore
from qgis.core import Qgis, QgsField, QgsWkbTypes

QT_MAJOR = int(QtCore.QT_VERSION_STR.split(".")[0])


def first_attr(*getters):
    """Return the first getter result that does not raise AttributeError."""
    for get in getters:
        try:
            return get()
        except AttributeError:
            continue
    raise AttributeError("No compatible attribute found")


GEOM_POINT = first_attr(lambda: Qgis.GeometryType.Point, lambda: QgsWkbTypes.PointGeometry)
GEOM_LINE = first_attr(lambda: Qgis.GeometryType.Line, lambda: QgsWkbTypes.LineGeometry)
GEOM_POLYGON = first_attr(lambda: Qgis.GeometryType.Polygon, lambda: QgsWkbTypes.PolygonGeometry)

GEOS_VALIDATOR = first_attr(
    lambda: Qgis.GeometryValidationEngine.Geos,
    lambda: __import__("qgis.core", fromlist=["QgsGeometry"]).QgsGeometry.ValidatorGeos,
)

METERS = first_attr(
    lambda: Qgis.DistanceUnit.Meters,
    lambda: __import__("qgis.core", fromlist=["QgsUnitTypes"]).QgsUnitTypes.DistanceMeters,
)

MSG_INFO = first_attr(lambda: Qgis.MessageLevel.Info, lambda: Qgis.Info)
MSG_WARNING = first_attr(lambda: Qgis.MessageLevel.Warning, lambda: Qgis.Warning)
MSG_CRITICAL = first_attr(lambda: Qgis.MessageLevel.Critical, lambda: Qgis.Critical)
MSG_SUCCESS = first_attr(lambda: Qgis.MessageLevel.Success, lambda: Qgis.Success)

try:  # Qt6 moved QAction to QtGui
    from qgis.PyQt.QtGui import QAction  # noqa: F401
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction  # noqa: F401


def layer_filter(kind):
    """Filter flag for QgsMapLayerComboBox: 'polygon' or 'line'."""
    try:
        return {"polygon": Qgis.LayerFilter.PolygonLayer, "line": Qgis.LayerFilter.LineLayer}[kind]
    except AttributeError:
        from qgis.core import QgsMapLayerProxyModel

        return {
            "polygon": QgsMapLayerProxyModel.PolygonLayer,
            "line": QgsMapLayerProxyModel.LineLayer,
        }[kind]


def make_field(name, kind, length=0):
    """Create a QgsField of kind 'str', 'int' or 'double' on any QGIS version."""
    type_names = {"str": "string", "int": "integer", "double": "double"}
    try:
        from qgis.PyQt.QtCore import QMetaType

        types = {
            "str": QMetaType.Type.QString,
            "int": QMetaType.Type.Int,
            "double": QMetaType.Type.Double,
        }
        return QgsField(name, types[kind], type_names[kind], length)
    except (AttributeError, TypeError):
        from qgis.PyQt.QtCore import QVariant

        types = {"str": QVariant.String, "int": QVariant.Int, "double": QVariant.Double}
        return QgsField(name, types[kind], type_names[kind], length)


def rubber_icon(name):
    """QgsRubberBand icon enum by short name ('circle', 'box', 'x')."""
    from qgis.gui import QgsRubberBand

    attr = {"circle": "ICON_CIRCLE", "box": "ICON_BOX", "x": "ICON_X", "full_box": "ICON_FULL_BOX"}[name]
    return first_attr(
        lambda: getattr(QgsRubberBand.IconType, attr),
        lambda: getattr(QgsRubberBand, attr),
    )


def exec_dialog(dialog):
    """Run a modal dialog (exec_ on old PyQt5, exec on PyQt6)."""
    runner = getattr(dialog, "exec", None) or getattr(dialog, "exec_")
    return runner()
