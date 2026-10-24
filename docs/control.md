# Using local fan control

## Manual mode

Enable **Manual mode** in the Control tab to set direct fan and LED percentages. The values apply to the active stage and mode and are reapplied by the controller. Manual mode bypasses PID, VPD targets, fan ramping, soft temperature boosts, and hard maximum-temperature fan and LED overrides. This also allows manual LED output during night modes. The setting and percentages persist across restarts. Turn Manual mode off to return to the saved automatic PID or stage fan-range behavior.

Temperature and humidity readings show one decimal place when the device provides fractional values; whole-number readings stay compact.

Open the **Control** tab to configure per-stage VPD targets, fan limits, and optional soft and hard temperature limits. Saved VPD targets, LED levels, and light schedules are written to matching Lisa Pro phase settings when saved, reapplied when OpenLisaPro starts, and reconciled once per minute. Saved fan limits are also reapplied when PID is off; while PID is on, its live fan command uses those device fields. PID tuning and temperature limits are local controller settings. The **Device** tab contains other growbox settings. PID tuning controls are collapsed by default.

## Enable and tune the controller

The local PID controller is enabled by default for new installations. Toggle it in the Control tab, or set `LISA_PRO_PID_ENABLED=0` to change the default for new or reset settings. Existing saved settings and the Control tab toggle take precedence.

Each stage can have separate VPD targets and fan limits for Day, Day silent, Night, and Night silent. The configured light-on and light-off schedule determines day and night, including schedules that cross midnight. The growbox's silent state selects the silent mode. Night and Night silent LED output is fixed at 0%.

The controller uses inside VPD as its goal and estimates the ventilation limit from outside dew point. It ramps fan changes to avoid abrupt speed shifts. The live status shows the current command and, while ramping, its target.

## Temperature protection

Each stage has an **Enable soft temperature fan boost** switch, optional soft minimum and maximum values, and a **Maximum fan boost** setting. The cap is an added fan percentage-point limit for that stage; it defaults to 20%, and setting it to 0 disables the soft boost. Above the soft maximum, the controller starts with a 5 percentage-point fan boost. It checks the temperature trend every minute and adds 10 points if temperature is flat or rising; it backs off by 10 points only after temperature falls by more than 0.1°C. This keeps increasing fan demand until temperature responds, subject to the per-stage maximum, the global **Temperature weight %/°C**, and the stage fan limit. It never reduces the VPD-based request. Changing the soft maximum or boost cap starts a fresh attempt. A hard temperature limit still commands the fan ceiling immediately. The soft minimum does not lower fan speed. Hover over the enable control for a short explanation.

The separate hard **Maximum temperature** takes priority over normal VPD control and requests the configured maximum fan speed. Normal control resumes after the temperature falls at least 0.5°C below the limit.

When outside temperature is at or above the hard limit, ventilation cannot cool below that limit. The controller requests maximum exhaust and immediately halves the active daytime LED setting. Otherwise, if inside temperature remains above the limit with the fan at maximum for 90 seconds, active daytime LED power is reduced by 10 percentage points. Further reductions are spaced 90 seconds apart. Output stays at or above 20% and 25% of its original setting (a maximum reduction of 75%). LED power is restored after recovery or when local PID is disabled.

## Chart and flow log

The **Chart** tab opens to a compact 48-hour fan and VPD chart. The chart loads its recent samples from the SQLite telemetry database across restarts, while the database keeps the full measurement history. Toggle lines, choose 1/6/24/48-hour presets, zoom in or out, and use Older/Newer to move within the chart's 48-hour window. Fan percentage and VPD use separate scales. Download the full dataset from the chart's JSON Lines or CSV links.

The calculation flowchart highlights the route used for the current fan request. Its collapsed **Recent flow changes** log stores changes and the active settings snapshot in SQLite, with no automatic expiry, and shows 20 entries per page. Each entry includes the weekday, date, and time. When fan request data is available, the entry shows the target before and after the change, the percentage-point difference, the temperature boost, and the current command. Select an entry to highlight its path.

For formulas, defaults, and controller priority rules, see [Specifications](specifications.md).
