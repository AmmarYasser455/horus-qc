"""Run the Horus QC test suite with QGIS's Python.

Windows:  "C:\\Program Files\\QGIS <version>\\bin\\python-qgis.bat" tests\\run_tests.py
Linux:    QT_QPA_PLATFORM=offscreen python3 tests/run_tests.py
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from qgis.core import QgsApplication  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication  # noqa: E402

# keep test settings away from a real QGIS profile
QCoreApplication.setOrganizationName("HorusQcTests")
QCoreApplication.setApplicationName("HorusQcTests")

app = QgsApplication([], True)
app.initQgis()

suite = unittest.defaultTestLoader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT)
result = unittest.TextTestRunner(verbosity=2).run(suite)
app.exitQgis()
sys.exit(0 if result.wasSuccessful() else 1)
