# Changelog

Notable changes to OpenLisaPro are recorded here. Dates use the release day.

## [0.1.26] - 2026-10-11

### Fixed

- Keep a small soft-temperature fan correction while still above the configured soft maximum, retry the initial boost when the maximum or boost cap changes, and keep the flowchart branch in sync with the active threshold.
- Add integration coverage for saved settings reaching the device and changes propagating through fan calculations when settings are changed and restored.
- Persist per-stage light schedules so periodic device reconciliation can restore them after external changes.

## [0.1.25] - 2026-10-11

### Changed

- Default the per-stage maximum soft temperature fan boost to 20 percentage points.

## [0.1.24] - 2026-10-11

### Changed

- Reconcile saved OpenLisaPro phase settings to Lisa Pro once per minute, restoring configured values if they change on the device while preserving the active PID fan command.

## [0.1.23] - 2026-10-11

### Changed

- Scale the fan and VPD chart height with the available window space.

## [0.1.22] - 2026-10-11

### Changed

- Reapply saved fan limits, VPD targets, LED levels, and light schedules to matching Lisa Pro phase settings at startup and when saved.
- Record soft temperature boost increases and backoffs, including the adjustment before and after each change.

## [0.1.21] - 2026-10-11

### Added

- Configure a maximum soft-temperature fan boost for each stage.

## [0.1.20] - 2026-10-11

### Changed

- Ramp soft temperature fan boost from a small initial correction, increasing only while temperature rises and backing off when it is flat or falling.

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
