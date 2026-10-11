# Changelog

Notable changes to OpenLisaPro are recorded here. Dates use the release day.

## [0.1.19] - 2026-10-11

### Fixed

- Reapply saved LED percentages to the growbox at controller startup, preventing device defaults from overriding lower configured limits after a restart.

## [0.1.18] - 2026-10-11

### Added

- Persist controller flow changes for 14 days and browse them 20 entries per page.
- Show the fan target before and after a change, the percentage-point difference, the temperature boost, and the current command when available.

### Changed

- Move README documentation links near the top and split setup, control, development, and Python client guidance into dedicated docs.
- Restore the configured LED percentage on startup when it changed while a temporary temperature override was active.

## [0.1.17] - 2026-10-11

### Changed

- Show the weekday and date with each controller flow change.
- Open the fan and VPD chart to a compact 48-hour view with smaller labels and controls.
