# Changelog

Notable changes to OpenLisaPro are recorded here. Dates use the release day.

## [0.1.56] - 2026-10-27

### Changed

- Estimate the VPD effect of outdoor air exchange using mixed-air temperature and moisture, and limit VPD-driven fan increases to a quiet 10 percentage points above the configured minimum.

## [0.1.55] - 2026-10-27

### Changed

- Make the DLI reading's click-through to the recommendations guide more obvious.

## [0.1.54] - 2026-10-27

### Added

- Add an accessible DLI recommendations guide with weekly stage targets, approximate plant-age ranges, a chart, and a highlighted recommendation for the active stage and grow day.

## [0.1.53] - 2026-10-27

### Changed

- Move the VPD target into the compact controller status row.

## [0.1.52] - 2026-10-27

### Changed

- Render the DLI unit in white for better contrast in the dashboard readings.

## [0.1.51] - 2026-10-27

### Changed

- Add an approximate DLI dashboard reading based on ToF distance, LED output, and the active stage's light schedule; size its unit to fit beside the value.
- Use smaller type for the DLI unit so it fits beside the reading.

## [0.1.50] - 2026-10-27

### Added

- Estimate daily light integral from FluxShield Habibi 150 Enhanced PPFD data, ToF distance, LED setting, and the active phase's light schedule.

## [0.1.49] - 2026-10-27

### Fixed

- Sync replica history before sending its heartbeat to the primary.

## [0.1.48] - 2026-10-27

### Added

- Show the device's ToF distance metric on the dashboard, with a German translation.

## [0.1.47] - 2026-10-26

### Changed

- Compress telemetry JSON values when compression makes them smaller, and omit the unused measurement settings index in new databases.

## [0.1.46] - 2026-10-26

### Changed

- Explain that preparing large compressed exports can take a moment.

### Fixed

- Set ZIP entry timestamps to the time the export is generated instead of the ZIP format's default date.

## [0.1.45] - 2026-10-26

### Changed

- Package JSON Lines and CSV data exports in ZIP archives, spooling large archives to disk to limit memory use.

## [0.1.44] - 2026-10-26

### Fixed

- Prevent export failures by negotiating gzip only when requested and using an incremental gzip stream.

## [0.1.43] - 2026-10-26

### Fixed

- Keep gzip compression on export transfers while saving normal JSON Lines and CSV files that open without manual decompression.

## [0.1.42] - 2026-10-26

### Changed

- Download measurement exports as gzip-compressed JSON Lines and CSV files.
- Mark legacy chart-only measurements in exports and preserve that marker during replication.
- Fall back to available controller or chart values when device metrics are null.

## [0.1.41] - 2026-10-26

### Changed

- Translate the day/night indicator caption and values into German.

## [0.1.40] - 2026-10-26

### Fixed

- Use the controller's stable Day/Night mode for the dashboard indicator, avoiding flicker when LED output is 0%.

## [0.1.39] - 2026-10-26

### Changed

- Show the active day/night period in a larger, separate dashboard field.

### Fixed

- Keep the schedule-based day/night indicator stable when the device briefly reports its LED as off at 0% output.

## [0.1.38] - 2026-10-24

### Fixed

- Keep a reachable primary in replica mode when a history backfill or heartbeat request times out.

## [0.1.37] - 2026-10-24

### Changed

- Keep event history replication compatible with 0.1.35 primaries by backfilling their paginated SQLite measurement and flow-event APIs.

## [0.1.36] - 2026-10-24

### Added

- Replicate measurements, flow events, and their settings snapshots from the primary into the replica's SQLite database.
- Resume event history synchronization from persisted cursors and avoid duplicate records when a batch is retried.

## [0.1.35] - 2026-10-24

### Added

- Store controller measurements, full device status, controller state, settings snapshots, and flow events in SQLite without automatic expiry.
- Expose full-history JSON Lines and CSV exports.

### Changed

- Keep the interactive chart focused on the latest 48 hours while retaining the full dataset in the SQLite database.

## [0.1.34] - 2026-10-24

### Added

- Add an English/German UI language switch with persistent selection and localized dashboard text, charts, and dates.

## [0.1.33] - 2026-10-24

### Changed

- Make LED, fan limit, and VPD target values directly editable from their displayed values.

## [0.1.32] - 2026-10-24

### Changed

- Show each HA node's IP in the status banner and link to the other node's UI.
- Keep the primary's config authoritative on every replica sync and reapply it to the growbox after failback.
- Add failback regression coverage for replica setting changes and subsequent config drift.

## [0.1.31] - 2026-10-24

### Changed

- Reduce the master/replica status banner and place it beside the version check in the header.

## [0.1.30] - 2026-10-24

### Added

- Add primary and replica high availability with five-second configuration sync, health checks, and controller failover.
- Show master and replica status and make replica settings read-only.
- Add tests for HA config sync, read-only enforcement, and promotion and demotion.

## [0.1.29] - 2026-10-11

### Added

- Add a complete manual mode with direct fan and LED percentages that bypass PID, VPD, and temperature controls.
- Show one decimal place for fractional temperature and humidity readings.
- Refresh desktop and mobile dashboard screenshots.

## [0.1.28] - 2026-10-11

### Changed

- Compare the running package release version with GitHub version tags instead of comparing an embedded source commit with the latest `main` commit.
- Embed the package release version in Docker images as `APP_VERSION`.

## [0.1.27] - 2026-10-11

### Changed

- Start soft-temperature fan boosts at 5 percentage points and add 10 points each minute while temperature is flat or rising; back off only after a measurable temperature decrease.
- Explain the soft-temperature boost behavior on hover in the stage controls.
- Expand controller tests for rising, flat, falling, and capped temperature responses.

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

- Persist controller flow changes and browse them 20 entries per page.
- Show the fan target before and after a change, the percentage-point difference, the temperature boost, and the current command when available.

### Changed

- Move README documentation links near the top and split setup, control, development, and Python client guidance into dedicated docs.
- Restore the configured LED percentage on startup when it changed while a temporary temperature override was active.

## [0.1.17] - 2026-10-11

### Changed

- Show the weekday and date with each controller flow change.
- Open the fan and VPD chart to a compact 48-hour view with smaller labels and controls.
