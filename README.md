# Lisa Pro UI

Modern Flask control panel and Python client for the **Lisa Pro / LuminaGrowX** growbox HTTP API.

Includes a **local fan PID** that takes over exhaust control by writing `fan_min = fan_max` on the device, using saved VPD / fan-limit overrides and outside VPD as a reachability cap.

## Quick start

```bash
uv sync
LISA_PRO_URL=http://192.168.178.242 uv run lisa-pro-ui
```

Open [http://127.0.0.1:5050](http://127.0.0.1:5050).

| Env var | Default | Purpose |
|---------|---------|---------|
| `LISA_PRO_URL` | `http://192.168.178.242` | Device base URL |
| `HOST` | `0.0.0.0` | Bind address |
| `PORT` | `5050` | Bind port |
| `FLASK_DEBUG` | `0` | Flask debug mode |
| `LISA_PRO_DATA` | `./data` | Local control config directory |

## Python client

```python
from lisa_pro_ui import LisaProClient

with LisaProClient("http://192.168.178.242") as box:
    print(box.status()["temp_c"])
    box.grow_start(total_days=90, seed="Basil", start_day=1)
```

API surface is documented in `openapi.yaml`.

## Tests

```bash
uv run pytest
```
