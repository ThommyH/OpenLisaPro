# Python client

`LisaProClient` is a typed wrapper for the Lisa Pro / LuminaGrowX HTTP API. Install the project in your environment, then use the client as a context manager:

```python
from lisa_pro_ui import LisaProClient

with LisaProClient("http://192.168.1.50") as box:
    status = box.status()
    print(status["temp_c"])
    box.grow_start(total_days=90, seed="Basil", start_day=1)
```

The client supports device status, grow and drying controls, phase and settings management, network operations, notifications, and update routes. `LisaProError` includes the HTTP status and response body when the device reports an error.

See [`openapi.yaml`](../openapi.yaml) for the API reference and [`examples/`](../examples/) for sample payloads.
