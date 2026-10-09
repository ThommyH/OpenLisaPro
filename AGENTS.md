# Repository Guide

## Project overview

OpenLisaPro is a Python 3.9+ Flask control panel and Python HTTP client for the Lisa Pro / LuminaGrowX growbox API. The Flask app serves a single-page UI and proxies device operations. It also stores local fan/VPD/LED overrides and can run a background VPD-to-fan PID controller.

## Layout

- `src/lisa_pro_ui/app.py`: Flask app factory, local control endpoints, and device proxy endpoints. `create_app(..., start_controller=False)` is useful for isolated app use and tests.
- `src/lisa_pro_ui/client.py`: `LisaProClient`, the typed wrapper around device HTTP routes; `LisaProError` carries device status/body details.
- `src/lisa_pro_ui/controller.py`: background fan PID loop, phase/mode selection, command ramping, and recent history.
- `src/lisa_pro_ui/config_store.py`: synchronized local JSON settings, normalization, defaults, and migration/seeding from device phases.
- `src/lisa_pro_ui/pid.py` and `vpd.py`: standalone PID and vapor-pressure-deficit calculations.
- `src/lisa_pro_ui/templates/index.html`, `static/css/app.css`, and `static/js/app.js`: Flask-rendered UI template, styling, and browser behavior.
- `openapi.yaml`: reverse-engineered device API reference; `examples/` contains sample payloads.
- `tests/`: pytest coverage for control API, client/controller behavior, persistence, PID, and VPD.

## Development

- Dependencies and packaging use `uv`; the configured console script is `lisa-pro-ui` (entry point `lisa_pro_ui:main`). The README's launch example uses `openlisapro`, so keep documentation and the configured script aligned if changing the command name.
- Install dependencies with `uv sync`.
- Run the app with `LISA_PRO_URL=http://<device-ip> uv run lisa-pro-ui`. The UI listens on port 5050 by default.
- Run the test suite with `uv run pytest`.
- Environment settings: `LISA_PRO_URL` selects the device URL, `LISA_PRO_DATA` selects the local config directory, and `HOST`, `PORT`, and `FLASK_DEBUG` control Flask serving. Local settings are written to `control.json` under the configured data directory.

## Implementation conventions

- Keep device HTTP details in `LisaProClient`; use its context manager so owned HTTP clients are closed. Flask routes generally translate client failures to JSON errors through `_err`.
- Build app-dependent behavior through `create_app` and its injected device URL, data directory, and controller start option. Tests use Flask's test client and temporary directories rather than a live device.
- Protect shared controller/config state with locks and return deep copies from persisted/snapshot data paths. Keep the PID loop resilient to individual tick failures.
- Normalize user/device values at the config boundary: phase/mode maps use string phase IDs and `day`, `night`, `night_silent`; fan and LED percentages are bounded to 0–100; VPD ranges are bounded and ordered.
- When adding or changing device routes, consult/update `openapi.yaml` as appropriate and use `examples/` for representative payload shapes.
- The browser UI talks to the Flask `/api/*` routes, not directly to the growbox. Keep template element IDs, JavaScript selectors, and API payload shapes in sync.
- No formatter or linter is configured in `pyproject.toml`; follow the existing Python style and keep changes focused.

## Hardware and side effects

The proxy endpoints can change grow/drying state, phase settings, network configuration, and device updates. The local PID controller writes fan settings to hardware when enabled. Do not assume a live device is available in routine development; use fixtures/mocks and tests for device-facing logic.
