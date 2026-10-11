# Development, tests, and releases

## Set up and test

Install the development dependency group and run the test suite:

```bash
uv sync --group dev
uv run python -m pytest -q
```

The tests use Flask's test client, fake device clients, and temporary data directories. They do not require a growbox.

For isolated Flask use, `create_app(device_url=..., data_dir=..., start_controller=False)` creates the application without starting its background controller.

## Build

Build the Python source distribution and wheel with:

```bash
uv build
```

Build Docker images tagged with the package version and `latest`:

```bash
make build
```

The Makefile defaults to `192.168.178.69:5555/open-lisa-pro`. Override `REGISTRY` and `IMAGE_NAME` as needed. `APP_GIT_COMMIT` is embedded in the image so the dashboard can report whether it is running the latest `main` commit.

## Release checklist

1. Update `version` in `pyproject.toml` and refresh `uv.lock` with `uv lock`.
2. Update the README, specifications, and [`CHANGELOG.md`](../CHANGELOG.md) when user-visible behavior changes.
3. Run `uv sync --group dev`, `uv run python -m pytest -q`, and `uv build`.
4. Commit the release changes and create an annotated tag matching the version, for example `0.1.18`.
5. Build the Docker image after committing so `APP_GIT_COMMIT` points at the release commit. Push the versioned image and `latest` to the configured registry when publishing the container.
6. Push the commit and tag, then publish the GitHub release with concise release notes.

## Project layout

- `src/lisa_pro_ui/app.py`: Flask app factory and device proxy routes
- `src/lisa_pro_ui/client.py`: typed growbox HTTP client
- `src/lisa_pro_ui/controller.py`: background fan PID loop and history
- `src/lisa_pro_ui/config_store.py`: local settings, defaults, and normalization
- `src/lisa_pro_ui/pid.py`, `vpd.py`: PID and VPD calculations
- `src/lisa_pro_ui/templates/`, `static/`: dashboard UI
- `openapi.yaml`: growbox API reference
- `tests/`: automated tests
