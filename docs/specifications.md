# OpenLisaPro specifications

This document describes the user-visible behavior of OpenLisaPro's local dashboard and fan controller. Device API details are documented separately in [`../openapi.yaml`](../openapi.yaml).

## Application and persistence

- OpenLisaPro is a Flask application and Python client for Lisa Pro / LuminaGrowX growboxes. The browser communicates with Flask; Flask communicates with the growbox HTTP API.
- The dashboard displays inside and outside temperature, relative humidity, dew point, VPD, device state, stage, and fan readings.
- Local control settings are stored in `control.json` in the configured data directory. Stage and mode overrides are local to OpenLisaPro; device factory presets remain separate until a device write is requested.
- Local PID control is enabled for new/reset settings by default. `LISA_PRO_PID_ENABLED=0` changes that default. A saved setting or the Control tab toggle takes precedence.
- Modes are `day`, `day_silent`, `night`, and `night_silent`. The active phase's light on/off schedule defines day and night, including schedules crossing midnight. The growbox silent schedule/active state selects the silent variant.

## Stage and global controls

Each stage and mode can have VPD min/max targets and fan min/max limits. Stage temperature settings include:

- **Soft minimum / maximum:** optional temperature bounds shown in the stage controls. Exceeding the soft maximum adds a secondary fan request. The soft minimum does not reduce the fan request.
- **Maximum temperature:** optional hard temperature limit. Above this limit the controller prioritizes cooling and requests the mode's configured maximum fan speed.
- **LED:** Day and Day silent output can be set separately. Night and Night silent LED output is always 0%.

Global PID settings include proportional, integral, and derivative gains; integral limit; deadband; update interval; ramp rate; temperature weight; unreachable-target hold/step settings. Defaults for new/reset configurations are defined in `src/lisa_pro_ui/config_store.py` (`DEFAULT_PID`).

## Fan calculation and priority

1. Resolve the current stage and mode from device status, the phase schedule, and silent-mode state. Read that mode's VPD target band and fan limits.
2. Use the lower VPD bound as the selected target. Estimate the maximum VPD ventilation can achieve by evaluating outside-air dew point at the current inside temperature. Cap the selected target at that estimate when needed.
3. Feed selected-target minus inside VPD to the PID. Within the deadband, request the configured fan minimum; when inside VPD is below target, map positive PID output to a higher fan request up to the configured maximum. The VPD fan request is the baseline.
4. If inside temperature exceeds the stage's soft maximum, add `temperature difference × Temperature weight` percentage points to the VPD fan request. This boost applies even when VPD is outside its target band. It can raise the request above what VPD alone would request, but never lowers that request. The result remains bounded by the stage/mode fan limits. The default weight is 2 percentage points per °C; zero disables the boost.
5. If inside temperature exceeds the hard maximum, the hard protection takes priority and requests the configured maximum fan speed. The normal ramp does not delay this safety command. Soft/VPD control resumes after temperature falls at least 0.5°C below the hard limit.
6. Otherwise, ramp toward the combined request at the configured rate (default 15 percentage points per minute, updated every 5 seconds). The displayed command may therefore trail the ramp target.
7. If the VPD target is unreachable due to outside-air moisture, or VPD has not improved while at maximum fan for the hold period, the controller can reduce fan demand in steps. It does not back off while the soft maximum is exceeded, so the temperature boost is preserved. Hard temperature protection also disables this backoff.

The fan command is written to the growbox as equal fan minimum and maximum values. Disabling local PID restores the configured fan range.

## Hard temperature and LED response

- If outside temperature is at or above the hard maximum, ventilation cannot cool below the limit. The controller requests maximum exhaust, shows a warning, and immediately halves the active Day or Day silent LED setting.
- If outside temperature is below the hard maximum, the controller requests maximum exhaust. If inside temperature remains above the limit for 90 seconds while the fan is at maximum, it reduces active daytime LED power by 10 percentage points. Further reductions are spaced by 90 seconds.
- LED power is never reduced below 20% or below 25% of its original setting (a maximum reduction of 75%). Night modes already use 0% LED.
- When inside temperature falls 0.5°C below the hard limit, the controller restores the configured LED setting. LED temperature overrides are temporary and are also restored when local PID is disabled.

## User interface

- The UI supports desktop and mobile layouts and exposes the current fan command, ramp target, controller state, and calculation flowchart.
- The flowchart highlights the decisions used for the current fan request, including outside-dew-point target limiting, PID/deadband branch, soft-temperature boost, and command/ramp.
- PID tuning controls are hidden by default. Stage controls include schedule times, VPD and fan ranges, soft temperature bounds, hard maximum temperature, and stage reset.
- Settings and device operations can write to growbox hardware. Controller and persistence behavior should be developed/tested with mocks rather than assuming a live device is available.

## Verification

Controller and app behavior is covered by `tests/`. Run the suite with `uv run pytest`; the tests use fake clients and temporary config directories and do not require growbox hardware.
