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

Open `http://<host-address>:5050` from a device that can reach the Docker host. The named volume is mounted at `/data`; its files are managed by Docker and do not appear in the repository's `data/` directory. Current local settings are in `/data/control.json`. The SQLite database `/data/openlisapro.sqlite3` stores controller measurements, full device status payloads, controller state, a settings snapshot for each distinct active configuration, and flow events. Existing JSON history files are imported once on startup and are no longer updated.

The image runs as UID 10001. On startup, the entrypoint assigns the mounted data directory and control files to that user. A bind mount such as `/path/on/host/data:/data` changes the host directory's ownership to UID 10001.

`make build` tags the image with the package version and `latest`. Set `REGISTRY` or `IMAGE_NAME` to change the default image destination (`192.168.178.69:5555/open-lisa-pro`).

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `LISA_PRO_URL` | `http://192.168.178.242` | Growbox base URL |
| `LISA_PRO_DATA` | `./data` | Directory for local settings, controller samples, and flow event history |
| `LISA_PRO_PID_ENABLED` | `1` | Default local PID state for new/reset settings; set to `0` to disable by default |
| `HOST` | `0.0.0.0` | Web server bind address |
| `PORT` | `5050` | Web server port |
| `FLASK_DEBUG` | `0` | Enable Flask debug mode when set to `1` |
| `APP_VERSION` | Build-provided | Release version shown by the update indicator; Docker builds set this from `pyproject.toml` |
| `LISA_PRO_HA_ROLE` | `primary` | Set to `replica` on the backup OpenLisaPro instance |
| `LISA_PRO_HA_NAME` | `OpenLisaPro` | Name shown for this instance in the HA status banner |
| `LISA_PRO_HA_MASTER_URL` | empty | Primary OpenLisaPro base URL; required on a replica (for example `http://192.168.1.51:5050`) |
| `LISA_PRO_HA_PUBLIC_URL` | Auto-detected | URL this node advertises to its peer so the HA banner can link to the other UI; set it explicitly when running behind Docker, NAT, or a reverse proxy |

The controller records a measurement on each tick (5 seconds by default). The SQLite history has no automatic retention limit. Download the full dataset from `/api/data/export?format=jsonl` or `/api/data/export?format=csv`; `/api/data/measurements?page=1&page_size=100` provides paginated records with raw measurements, controller state, and the associated settings snapshot. The interactive chart still loads only its latest 48 hours.

Set `LISA_PRO_HA_PUBLIC_URL` to each instance's reachable host URL when auto-detection returns a Docker bridge address. For example, if the primary host is `192.168.178.69` and the replica host is `192.168.178.142`, set:

```bash
# Primary
LISA_PRO_HA_PUBLIC_URL=http://192.168.178.69:5050

# Replica
LISA_PRO_HA_PUBLIC_URL=http://192.168.178.142:5050
```

Set the value in each container's environment and restart that instance. For one primary and one replica, configure both instances with the same `LISA_PRO_URL`. Set the primary's role to `primary` (the default), and set the replica's role and master URL as above. The replica fetches and persists the primary's control configuration every five seconds. When the primary health endpoint stops responding, the replica promotes itself and starts its control loop; when the primary responds again, it returns to replica mode. Keep each instance's data directory separate. Both instances need network access to the growbox, and the replica must be able to reach the primary on its OpenLisaPro port.

Keep the app on a trusted network. It can change growbox settings and fan operation. See [Using local fan control](control.md) before enabling the controller.
