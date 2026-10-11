# OpenLisaPro

OpenLisaPro is a self-hosted Flask dashboard and Python client for Lisa Pro / LuminaGrowX growboxes. It displays live climate and device status, manages local control settings, and can run a VPD-based fan controller.

The browser communicates with the local Flask app, which proxies requests to the growbox. Local settings are stored in `control.json`; fan and VPD samples are retained in `controller-history.jsonl` for 48 hours, and controller flow changes in `flow-events.json` for 14 days.

## Features

- Monitor inside and outside temperature, humidity, dew point, VPD, grow stage, and device status.
- Configure per-stage fan limits, VPD targets, temperature limits, LED levels, and light schedules.
- Optionally control exhaust fan speed with a local PID loop and temperature protection.
- Use complete manual mode to set fan and LED percentages directly, bypassing PID and temperature overrides.
- Show sensor temperature and humidity to one decimal place when device readings include fractions.
- Gradually boost fan speed above a stage's soft temperature limit until temperature starts falling, with a per-stage boost cap.
- Review a two-day fan and VPD chart, or page through 14 days of controller flow changes with weekday, date, time, and fan request details.
- Use `LisaProClient` to access the device API from Python.

## Documentation

- [Installation and configuration](docs/installation.md)
- [Using local fan control](docs/control.md)
- [Specifications](docs/specifications.md)
- [Python client](docs/python-client.md)
- [Development, tests, and release builds](docs/development.md)
- [Changelog](CHANGELOG.md)
- [Growbox HTTP API reference](openapi.yaml)

## Quick start

Requires Python 3.9+, [`uv`](https://docs.astral.sh/uv/), and a reachable growbox for live device features.

```bash
uv sync
LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050). Replace the example address with your device address. For setup options, Docker instructions, and environment variables, see [Installation](docs/installation.md).

## Screenshots

![OpenLisaPro dashboard at desktop width](docs/screenshots/dashboard-desktop.png)

![OpenLisaPro dashboard at mobile width](docs/screenshots/dashboard-mobile.png)

# OpenLisaPro

OpenLisaPro is a self-hosted Flask dashboard and Python client for Lisa Pro / LuminaGrowX growboxes. It displays live climate and device status, manages local control settings, and can run a VPD-based fan controller.

The browser communicates with the local Flask app, which proxies requests to the growbox. Local settings are stored in `control.json`; fan and VPD samples are retained in `controller-history.jsonl` for 48 hours.

## Features

- Monitor inside and outside temperature, humidity, dew point, VPD, grow stage, and device status.
- Configure per-stage fan limits, VPD targets, temperature limits, LED levels, and light schedules.
- Optionally control exhaust fan speed with a local PID loop and temperature protection.
- Review a two-day fan and VPD chart, or inspect recent controller flow changes with weekday, date, and time.
- Use `LisaProClient` to access the device API from Python.

## Quick start

Requires Python 3.9+, [`uv`](https://docs.astral.sh/uv/), and a reachable growbox for live device features.

```bash
uv sync
LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050). Replace the example address with your device address. For setup options, Docker instructions, and environment variables, see [Installation](docs/installation.md).

## Screenshots

![OpenLisaPro dashboard at desktop width](docs/screenshots/dashboard-desktop.png)

![OpenLisaPro dashboard at mobile width](docs/screenshots/dashboard-mobile.png)

## Documentation

- [Installation and configuration](docs/installation.md)
- [Using local fan control](docs/control.md)
- [Specifications](docs/specifications.md)
- [Python client](docs/python-client.md)
- [Development, tests, and release builds](docs/development.md)
- [Growbox HTTP API reference](openapi.yaml)
