# Installation and configuration

## Requirements

- Python 3.9 or newer
- [`uv`](https://docs.astral.sh/uv/)
- A Lisa Pro / LuminaGrowX device reachable over HTTP for live device features

## Run with uv

From the repository root:

```bash
uv sync
LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

Open `http://127.0.0.1:5050`. The app listens on `0.0.0.0` by default. To bind to localhost only or choose another port:

```bash
HOST=127.0.0.1 PORT=5050 LISA_PRO_URL=http://192.168.1.50 uv run lisa-pro-ui
```

## Run with Docker

Build the image and start it with persistent storage for local settings:

```bash
make build

docker run -d --name lisa-pro-ui --restart unless-stopped \
  -p 5050:5050 \
  -e LISA_PRO_URL=http://192.168.1.50 \
  -v lisa-pro-ui-data:/data \
  192.168.178.69:5555/open-lisa-pro:latest
```

Open `http://<host-address>:5050` from a device that can reach the Docker host. The named volume is mounted at `/data`; its files are managed by Docker and do not appear in the repository's `data/` directory. The settings file is `/data/control.json`.

The image runs as UID 10001. On startup, the entrypoint assigns the mounted data directory and control files to that user. A bind mount such as `/path/on/host/data:/data` changes the host directory's ownership to UID 10001.

`make build` tags the image with the package version and `latest`. Set `REGISTRY` or `IMAGE_NAME` to change the default image destination (`192.168.178.69:5555/open-lisa-pro`).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LISA_PRO_URL` | `http://192.168.178.242` | Growbox base URL |
| `LISA_PRO_DATA` | `./data` | Directory for `control.json` and controller history |
| `LISA_PRO_PID_ENABLED` | `1` | Default local PID state for new/reset settings; set to `0` to disable by default |
| `HOST` | `0.0.0.0` | Web server bind address |
| `PORT` | `5050` | Web server port |
| `FLASK_DEBUG` | `0` | Enable Flask debug mode when set to `1` |
| `APP_GIT_COMMIT` | Build-provided | Commit shown by the update indicator; Docker builds set this automatically |

Keep the app on a trusted network. It can change growbox settings and fan operation. See [Using local fan control](control.md) before enabling the controller.
