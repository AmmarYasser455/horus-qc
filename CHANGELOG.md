# Changelog

## 1.0.1 (2026-11-20)

- Review mode: the highlight now follows the building after Ctrl+Z / redo and edits made with other tools.

## 1.0.0 (2026-11-20)

First release.

- 28 checks (17 building, 11 road), measured in metres on any CRS
- Overlaps classified as duplicate / inside / overlap / shared-wall sliver; hairline gaps
- Building-on-road, set-back, grid-edge and outside-grid checks
- Road undershoot / overshoot / dead end / isolated piece / duplicate / short segment
- 16 one-click, undoable fixes (untangle bow-tie, cut overlap, snap wall, orthogonalize, remove spikes, snap undershoot, trim overshoot, …)
- Review mode: serpentine queue, E/F/N/Q keys, roof-to-base shift by floors (1–9), WASD nudge, daily target, status fields, daily report
- Instant check after every edit
- Background processing, error marker layer, CSV / GeoPackage export, ignore list saved in the project
- QGIS 3.22+ (Qt5) and QGIS 4 (Qt6)
