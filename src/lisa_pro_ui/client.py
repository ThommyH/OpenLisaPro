"""HTTP client for the Lisa Pro / LuminaGrowX growbox API."""

from __future__ import annotations

from typing import Any, Mapping, Optional

import httpx

DEFAULT_BASE_URL = "http://192.168.178.242"
DEFAULT_TIMEOUT = 15.0


class LisaProError(Exception):
    """Raised when the device API returns an error response."""

    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class LisaProClient:
    """Thin typed wrapper around the reverse-engineered device API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "LisaProClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- internals ---------------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Any:
        response = self._client.request(method, path, json=json, params=params)
        if response.status_code not in expected:
            body: Any
            try:
                body = response.json()
            except Exception:
                body = response.text
            message = body.get("error") if isinstance(body, dict) else str(body)
            raise LisaProError(
                message or f"HTTP {response.status_code}",
                status_code=response.status_code,
                body=body,
            )
        if response.status_code == 204 or not response.content:
            return None
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            return response.json()
        return response.text

    def get(self, path: str, **kwargs: Any) -> Any:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, json: Any = None, **kwargs: Any) -> Any:
        return self._request("POST", path, json=json, **kwargs)

    # ---- status / info -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        return self.get("/api/status")

    def info(self) -> dict[str, Any]:
        return self.get("/api/info")

    def setup_status(self) -> dict[str, Any]:
        return self.get("/api/setup/status")

    def ping(self) -> bool:
        try:
            self.status()
            return True
        except Exception:
            return False

    # ---- grow / drying -----------------------------------------------------

    def grow_start(
        self,
        *,
        total_days: int = 90,
        start_epoch: int | None = None,
        seed: str | None = None,
        start_day: int = 1,
    ) -> dict[str, Any]:
        import time

        epoch = start_epoch if start_epoch is not None else int(time.time()) - (start_day - 1) * 86400
        payload: dict[str, Any] = {
            "action": "start",
            "total_days": int(total_days),
            "start_epoch": int(epoch),
        }
        if seed is not None:
            payload["seed"] = seed
        return self.post("/api/grow", json=payload)

    def grow_stop(self) -> dict[str, Any]:
        return self.post("/api/grow", json={"action": "stop"})

    def grow_set_day(self, day: int) -> dict[str, Any]:
        """Re-affirm grow with a corrected day number (keeps cycle running)."""
        import time

        epoch = int(time.time()) - (int(day) - 1) * 86400
        return self.post("/api/grow", json={"action": "start", "start_epoch": epoch})

    def drying_start(self) -> dict[str, Any]:
        return self.post("/api/drying", json={"action": "start"})

    def drying_stop(self) -> dict[str, Any]:
        return self.post("/api/drying", json={"action": "stop"})

    # ---- settings ----------------------------------------------------------

    def get_silent(self) -> dict[str, Any]:
        return self.get("/api/settings/silent")

    def set_silent(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        return self.post("/api/settings/silent", json=dict(settings))

    def get_door_actions(self) -> dict[str, Any]:
        return self.get("/api/settings/door_actions")

    def set_door_actions(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        return self.post("/api/settings/door_actions", json=dict(settings))

    def get_phases(self) -> dict[str, Any]:
        return self.get("/api/settings/phases")

    def set_phases(self, phases: Mapping[str, Any] | list[Any]) -> dict[str, Any]:
        payload = phases if isinstance(phases, Mapping) and "phases" in phases else {"phases": phases}
        return self.post("/api/settings/phases", json=payload)

    def reset_phases(self) -> dict[str, Any]:
        return self.post("/api/settings/phases/reset")

    # ---- network / notify / mqtt / app -------------------------------------

    def set_network(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return self.post("/api/network", json=dict(payload))

    def wifi_scan(self, *, wait: bool = True, attempts: int = 20) -> Any:
        """Start or poll Wi-Fi scan. When wait=True, poll until results array arrives."""
        import time

        data = self._request("GET", "/api/wifi/scan", expected=(200, 202))
        if not wait:
            return data
        for _ in range(attempts):
            if isinstance(data, list):
                return data
            time.sleep(1)
            data = self._request("GET", "/api/wifi/scan", expected=(200, 202))
        return data

    def set_notify(
        self,
        *,
        enabled: bool,
        phone: str,
        apikey: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"enabled": enabled, "phone": phone}
        if apikey:
            payload["apikey"] = apikey
        return self.post("/api/notify", json=payload)

    def notify_test(self, *, phone: str, apikey: Optional[str] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"phone": phone}
        if apikey:
            payload["apikey"] = apikey
        return self.post("/api/notify/test", json=payload)

    def get_mqtt(self) -> dict[str, Any]:
        return self.get("/api/mqtt")

    def set_mqtt(self, settings: Mapping[str, Any]) -> dict[str, Any]:
        return self.post("/api/mqtt", json=dict(settings))

    def set_device_name(self, name: str) -> dict[str, Any]:
        return self.post("/api/app", json={"name": name})

    # ---- update ------------------------------------------------------------

    def update_check(self) -> dict[str, Any]:
        return self.get("/api/update/check")

    def update_progress(self) -> dict[str, Any]:
        return self.get("/api/update/progress")

    def update_remote(self) -> Any:
        return self._request("POST", "/api/update/remote", expected=(200, 202))
