# Using local fan control

Open the **Control** tab to configure per-stage VPD targets, fan limits, and optional soft and hard temperature limits. The **Device** tab contains growbox settings. PID tuning controls are collapsed by default.

## Enable and tune the controller

The local PID controller is enabled by default for new installations. Toggle it in the Control tab, or set `LISA_PRO_PID_ENABLED=0` to change the default for new or reset settings. Existing saved settings and the Control tab toggle take precedence.

Each stage can have separate VPD targets and fan limits for Day, Day silent, Night, and Night silent. The configured light-on and light-off schedule determines day and night, including schedules that cross midnight. The growbox's silent state selects the silent mode. Night and Night silent LED output is fixed at 0%.

The controller uses inside VPD as its goal and estimates the ventilation limit from outside dew point. It ramps fan changes to avoid abrupt speed shifts. The live status shows the current command and, while ramping, its target.

## Temperature protection

Each stage has an **Enable soft temperature fan boost** switch, optional soft minimum and maximum values, and a **Maximum fan boost** setting. The cap is an added fan percentage-point limit for that stage; it defaults to 100%, and setting it to 0 disables the soft boost. Above the soft maximum, the controller starts with a small fan boost. It checks the temperature trend every minute: if temperature is still rising, it adds 2 percentage points; if temperature is flat or falling, it removes 2 points. The boost is capped by the per-stage maximum, the global **Temperature weight %/°C**, and the stage fan limit, and never reduces the VPD-based request. A hard temperature limit still commands the fan ceiling immediately. The soft minimum does not lower fan speed.

The separate hard **Maximum temperature** takes priority over normal VPD control and requests the configured maximum fan speed. Normal control resumes after the temperature falls at least 0.5°C below the limit.

When outside temperature is at or above the hard limit, ventilation cannot cool below that limit. The controller requests maximum exhaust and immediately halves the active daytime LED setting. Otherwise, if inside temperature remains above the limit with the fan at maximum for 90 seconds, active daytime LED power is reduced by 10 percentage points. Further reductions are spaced 90 seconds apart. Output stays at or above 20% and 25% of its original setting (a maximum reduction of 75%). LED power is restored after recovery or when local PID is disabled.

## Chart and flow log

The **Chart** tab opens to a compact 48-hour fan and VPD chart. It retains up to two days of history across restarts. Toggle lines, choose 1/6/24/48-hour presets, zoom in or out, and use Older/Newer to move through retained samples. Fan percentage and VPD use separate scales.

The calculation flowchart highlights the route used for the current fan request. Its collapsed **Recent flow changes** log persists changes for 14 days and shows 20 entries per page. Each entry includes the weekday, date, and time. When fan request data is available, the entry shows the target before and after the change, the percentage-point difference, the temperature boost, and the current command. Select an entry to highlight its path.

For formulas, defaults, and controller priority rules, see [Specifications](specifications.md).
