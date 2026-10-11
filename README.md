# OpenLisaPro

OpenLisaPro is a self-hosted web dashboard and Python client for Lisa Pro / LuminaGrowX growboxes. The dashboard reads live climate and device status, lets you manage growbox settings, and can run a local fan controller that adjusts exhaust speed to help maintain a VPD target.

The web app runs on your own computer or server. It talks to the growbox over its HTTP API; the browser talks only to the local Flask app. Local VPD, fan, LED, schedule, and PID overrides are stored on disk in `control.json`; fan/VPD samples are persisted in `controller-history.jsonl` for two days.

## What it does

- Shows inside and outside temperature, relative humidity, dew point, and VPD, along with the current grow stage and device status.
- Lets you manage stage-specific hard and soft temperature limits, VPD targets, fan limits, separate Day and Day silent LED levels, and light-on/light-off schedules. Night and Night silent LED power is fixed at 0%.
- Runs a local PID controller for exhaust fan speed, enabled by default. It uses inside VPD as the control goal, estimates the ventilation limit from outside dew point at inside temperature, observes each stage's fan limits, and ramps changes to avoid abrupt speed shifts.
- Displays the controller's current mode, status, and ramp target, with an expandable explanation of the calculation and its states.
- Includes a collapsed event log beside the fan flowchart; selecting an entry highlights the path taken for that controller decision.
- Provides a two-day fan and VPD history chart with selectable lines, independent scales, range presets, and zoom/navigation controls, plus controls for growbox settings such as silent operation, door actions, and MQTT.
- Checks the running build commit against the latest commit on the repository's `main` branch and shows update status in the header. Abbreviated Docker commit hashes are matched against GitHub's full SHA.
- Includes `LisaProClient`, a small typed Python wrapper for the growbox API.

## What OpenLisaPro adds

The growbox continues to operate its own lighting, sensors, and built-in schedules. OpenLisaPro adds a local web control layer that reads the device API and can command exhaust fan settings. It stores its own per-stage VPD, fan, temperature, LED, and schedule overrides in `control.json`; these settings are separate from the growbox's factory presets.

For fan control, OpenLisaPro uses inside VPD as its main goal and estimates what ventilation can achieve from outside dew point. Its optional soft temperature maximum adds a low-gain correction above the VPD fan request, even when VPD is outside its configured range. Temperature control never lowers the VPD request: a drier-than-target result is preferable to leaving the air too humid. The global **Temperature weight %/°C** controls the correction (default 15 percentage points per degree; set to zero to turn it off). The separate hard maximum temperature remains a safety override and commands the configured maximum fan speed.

The UI can be viewed on desktop or mobile. The PID loop is enabled for new installations and can write fan settings to the connected device. Disable it with the Control tab or set `LISA_PRO_PID_ENABLED=0` to change the default for new or reset settings.

## Screenshots

These screenshots use mock device data and show the dashboard at desktop and mobile widths, including stage-specific soft temperature bounds, the global temperature correction weight, separate Day and Day silent LED controls, and the growbox silent schedule toggle. The example shows how the local fan controller combines VPD and temperature inputs while preserving the selected stage's fan limits. The mock server does not connect to a growbox.

![OpenLisaPro dashboard at desktop width](docs/screenshots/dashboard-desktop.png)

![OpenLisaPro dashboard at mobile width](docs/screenshots/dashboard-mobile.png)

See [Specifications](docs/specifications.md) for the full controller priorities, formulas, protection behavior, defaults, and UI requirements.

## Requirements

- Python 3.9 or newer
- [`uv`](https://docs.astral.sh/uv/) for dependency and environment management
- A Lisa Pro / LuminaGrowX device reachable over HTTP to use live device features

## Setup and run

Clone the repository, then install the project and its dependencies:

```bash
uv sync
```

Set the device's base URL and start the dashboard:

```bash
LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

Replace the example address with the address of your growbox. Open [http://127.0.0.1:5050](http://127.0.0.1:5050) in a browser on the same computer. To access the dashboard from another device on your local network, use the host computer's LAN address and keep the app bound to a trusted network.

The app binds to `0.0.0.0` by default. You can choose a different bind address and port with environment variables:

```bash
HOST=127.0.0.1 PORT=5050 LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

### Docker

Build and run the container, mounting a volume to preserve local control settings. `make build` builds the Dockerfile and tags the image with the project version and `latest`, embedding the current Git commit for the build status check. Override `REGISTRY` or `IMAGE_NAME` to use a different image destination. You can also build manually:

```bash
docker build -t lisa-pro-ui:latest .
docker run -d --name lisa-pro-ui --restart unless-stopped \
  -p 5050:5050 \
  -e LISA_PRO_URL=http://192.168.1.50 \
  -v lisa-pro-ui-data:/data \
  lisa-pro-ui:latest
```

Open `http://<host-address>:5050` from a device that can reach the Docker host. The example uses a named Docker volume (`lisa-pro-ui-data`) mounted at `/data`; its files are managed by Docker and do not appear in the repository's `data/` directory. The settings file inside the volume is `/data/control.json`. At startup, the container assigns the mounted data directory and control files to UID 10001, then runs the app as that unprivileged user. A bind mount such as `-v /path/on/host/data:/data` will therefore change that host directory's ownership to UID 10001.

`LISA_PRO_DATA` selects the directory for local control settings. It defaults to `./data`; the settings file is `control.json` inside that directory. Keep this file if you want to preserve your local overrides between runs.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LISA_PRO_URL` | `http://192.168.178.242` | Growbox base URL |
| `LISA_PRO_DATA` | `./data` | Directory for local control settings |
| `LISA_PRO_PID_ENABLED` | `1` | Default local PID state for new/reset settings; set to `0` to disable by default |
| `HOST` | `0.0.0.0` | Address for the web server to bind to |
| `PORT` | `5050` | Web server port |
| `FLASK_DEBUG` | `0` | Enable Flask debug mode when set to `1` |

## Using local fan control

Open the **Control** tab to configure per-stage VPD targets, fan limits, and optional soft temperature minimum/maximum values. Each stage has an **Enable soft temperature fan boost** switch, so you can disable its configured limits without clearing them. Local PID is enabled by default for new installations. Set `LISA_PRO_PID_ENABLED=0` to make the default disabled. Existing saved control settings and the Control tab toggle take precedence. The **Chart** tab keeps up to two days of fan and VPD history, including across app restarts. Toggle lines to simplify the view, use the 1/6/24/48 hour presets, zoom with +/−, and move through older samples with the navigation buttons. Fan speed and VPD use separate aligned charts with independent scales. The **Device** tab contains growbox settings. PID tuning settings are collapsed by default. Expand them to adjust the gains, ramp rate, update interval, deadband, and temperature correction weight.

The top-right build indicator checks the running commit against the latest commit on GitHub `main` at startup and periodically. Use its refresh button to check immediately. Docker builds include their source commit; development builds without `APP_GIT_COMMIT` can show the latest commit but cannot verify whether the checkout is current.

Enable local PID control to let OpenLisaPro command the exhaust fan. The live status shows the current fan command; while it is ramping, it also shows the target speed. The configured light-on and light-off times for the active stage determine whether the controller uses day or night settings. The LEDs' current on/off state does not define the scheduled day/night mode.

### Stage temperature protection

Set optional **Soft temperature min/max** values for each stage in the Control tab. When inside temperature exceeds the stage's soft maximum, OpenLisaPro adds a small proportional boost to the VPD fan request, whether or not VPD is inside its target band. This adjustment can only increase the request; it never reduces ventilation needed to meet the VPD target. The global PID setting **Temperature weight %/°C** sets the boost per degree (default 15; set it to 0 to disable). Fan commands always stay within the configured stage fan limits. The soft minimum does not lower fan speed below the VPD request.

The separate optional **Maximum temperature** is a hard safety limit. When inside temperature rises above it, the controller prioritizes cooling and commands that mode's configured maximum fan speed. VPD and soft temperature control resume after temperature falls at least 0.5°C below the hard limit. The PID ramp default for new or reset settings is 15% per minute.

Each stage has separate configurable **Day** and **Day silent** LED levels in its stage card. Night and Night silent always use 0% LED power. If temperature protection reduces LED power, it temporarily adjusts the active Day or Day silent value and restores that mode's configured value after recovery.

If outside temperature is at or above the stage limit, ventilation cannot cool the grow space below that limit. OpenLisaPro keeps the exhaust fan at its configured maximum and shows a warning. During a scheduled daytime mode it immediately halves that mode's LED setting; at night the LED is already off. If outside air is cooler than the limit, the active daytime mode's LED power is reduced by 10 percentage points after the fan has been at its maximum for 90 seconds without the inside temperature returning below the limit. Further reductions are spaced by 90 seconds. In either case, output never drops below 20% or below 25% of the original setting (so no more than 75% is removed). When the inside temperature falls 0.5°C below the limit, the active mode's configured LED level is restored. This temporary LED adjustment is written to the growbox phase settings and is restored by the controller after recovery or when local PID is disabled. Each stage can be reset to the same factory defaults used by **Reset defaults**, while leaving other stages unchanged. The fan calculation diagram highlights the route and command currently used by the controller.

### VPD target and outside dew point

Inside VPD remains the PID control goal because it reflects the current conditions around the plants. The controller also estimates the highest VPD ventilation can produce using outside dew point. Dew point represents incoming-air moisture content; the controller evaluates it at the inside temperature so that outside and inside VPD are compared at the same temperature. When the requested target is above this estimate, the PID uses the achievable limit and the dashboard shows both values.

For example, outside air at 22°C and 59% RH has a dew point near 13.6°C. At an inside temperature of 23°C, that moisture level corresponds to about 1.25 kPa VPD. A requested target of 1.50 kPa is therefore limited to about 1.25 kPa for ventilation control. This is an estimate of what ventilation alone can achieve; moisture produced inside the grow space can reduce the actual result.

The **Reset defaults** action restores local control defaults and the growbox's factory phase presets. Stage and device settings may be written to the connected hardware when you save or reset them.

Use **Enable growbox silent mode** or **Disable growbox silent mode** in the Control tab to toggle the device's silent schedule through its settings API. The growbox applies silent operation during the configured silent hours. The local PID uses the device's reported active state to select Day silent or Night silent fan limits.

## Python client

The package also exposes `LisaProClient` for scripts that need to call the device API directly:

```python
from lisa_pro_ui import LisaProClient

with LisaProClient("http://192.168.1.50") as box:
    status = box.status()
    print(status["temp_c"])
    box.grow_start(total_days=90, seed="Basil", start_day=1)
```

`LisaProClient` supports device status, grow and drying controls, phase and settings management, network operations, notifications, and update routes. `LisaProError` includes the HTTP status and response body when the device reports an error. See [`openapi.yaml`](openapi.yaml) for the documented API surface and [`examples/`](examples/) for sample payloads.

## Project layout

```text
src/lisa_pro_ui/
  app.py                 Flask routes and app factory
  client.py              Typed HTTP client for the growbox
  config_store.py        Local settings, defaults, and normalization
  controller.py          Background VPD-to-fan PID controller
  pid.py                 PID calculation
  vpd.py                 VPD and dew-point calculations
  templates/index.html   Dashboard markup
  static/css/app.css     Responsive dashboard styles
  static/js/app.js       Browser behavior and chart rendering
openapi.yaml             Growbox HTTP API reference
tests/                   Automated tests
```

## Development

Install development dependencies and run the test suite with:

```bash
uv sync --group dev
uv run pytest
```

For isolated app use, `create_app(device_url=..., data_dir=..., start_controller=False)` creates the Flask app without starting the background controller. The tests use Flask's test client and temporary settings directories; no physical growbox is needed for those tests.
