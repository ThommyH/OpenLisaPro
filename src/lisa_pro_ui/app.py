"""Flask control UI that proxies the Lisa Pro device API and runs local fan PID."""

from __future__ import annotations

import atexit
import os
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from lisa_pro_ui.client import DEFAULT_BASE_URL, LisaProClient, LisaProError
from lisa_pro_ui.config_store import ConfigStore
from lisa_pro_ui.controller import FanPidController

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(os.environ.get("LISA_PRO_DATA", PACKAGE_DIR.parent.parent / "data"))


def create_app(
    device_url: str | None = None,
    *,
    data_dir: Path | None = None,
    start_controller: bool = True,
) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PACKAGE_DIR / "templates"),
        static_folder=str(PACKAGE_DIR / "static"),
    )
    app.config["DEVICE_URL"] = (device_url or os.environ.get("LISA_PRO_URL") or DEFAULT_BASE_URL).rstrip("/")
    data_path = Path(data_dir or DEFAULT_DATA_DIR)
    store = ConfigStore(data_path / "control.json")
    controller = FanPidController(device_url=app.config["DEVICE_URL"], store=store)
    app.config["CONTROL_STORE"] = store
    app.config["FAN_CONTROLLER"] = controller

    def client() -> LisaProClient:
        return LisaProClient(app.config["DEVICE_URL"])

    @app.get("/")
    def index():
        return render_template("index.html", device_url=app.config["DEVICE_URL"])

    @app.get("/api/meta")
    def meta():
        return jsonify({"device_url": app.config["DEVICE_URL"]})

    def _ok(data):
        return jsonify(data if data is not None else {"ok": True})

    def _err(exc: Exception, code: int = 502):
        if isinstance(exc, LisaProError):
            return jsonify({"error": str(exc), "device_status": exc.status_code, "body": exc.body}), code
        return jsonify({"error": str(exc)}), code

    # ---- local fan / VPD control -------------------------------------------

    @app.get("/api/control")
    def control_get():
        snap = controller.snapshot()
        return jsonify(snap)

    @app.get("/api/control/history")
    def control_history():
        limit = request.args.get("limit", default=360, type=int)
        return jsonify({"history": controller.history(limit=limit or 360)})

    def _apply_led_to_device(led_map: dict) -> None:
        """Write local LED overrides onto device phase day/night settings."""
        if not isinstance(led_map, dict) or not led_map:
            return
        with client() as c:
            phases = c.get_phases().get("phases") or []
            changed = False
            for phase in phases:
                phase_id = str(phase.get("id", 0))
                modes = led_map.get(phase_id)
                if not isinstance(modes, dict):
                    continue
                settings = phase.setdefault("settings", {})
                for mode, led in modes.items():
                    if mode not in ("day", "night"):
                        continue
                    bucket = settings.setdefault(mode, {})
                    value = max(0.0, min(100.0, float(led)))
                    if float(bucket.get("led", -1)) != value:
                        bucket["led"] = value
                        changed = True
            if changed:
                c.set_phases({"phases": phases})

    @app.post("/api/control")
    def control_post():
        body = request.get_json(force=True, silent=True) or {}
        try:
            cfg = store.update(body)
        except Exception as exc:
            return _err(exc, 400)
        led_error = None
        if "led" in body:
            try:
                _apply_led_to_device(cfg.get("led") or {})
            except Exception as exc:  # noqa: BLE001 — local save should still succeed
                led_error = str(exc)
        payload = {"ok": True, "config": cfg, "state": controller.snapshot()["state"]}
        if led_error:
            payload["led_error"] = led_error
        return jsonify(payload)

    @app.post("/api/control/seed-fans")
    def control_seed_fans():
        """Pull current device phase fan/LED settings into local overrides (if missing)."""
        try:
            with client() as c:
                phases = c.get_phases().get("phases") or []
            cfg = store.ensure_fan_limits_from_phases(phases)
            return jsonify({"ok": True, "config": cfg})
        except Exception as exc:
            return _err(exc)

    # ---- device proxy ------------------------------------------------------

    @app.get("/api/proxy/status")
    def proxy_status():
        try:
            with client() as c:
                return _ok(c.status())
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/info")
    def proxy_info():
        try:
            with client() as c:
                return _ok(c.info())
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/setup-status")
    def proxy_setup_status():
        try:
            with client() as c:
                return _ok(c.setup_status())
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/silent")
    def proxy_get_silent():
        try:
            with client() as c:
                return _ok(c.get_silent())
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/silent")
    def proxy_set_silent():
        try:
            with client() as c:
                return _ok(c.set_silent(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/door-actions")
    def proxy_get_door():
        try:
            with client() as c:
                return _ok(c.get_door_actions())
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/door-actions")
    def proxy_set_door():
        try:
            with client() as c:
                return _ok(c.set_door_actions(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/phases")
    def proxy_get_phases():
        try:
            with client() as c:
                return _ok(c.get_phases())
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/phases")
    def proxy_set_phases():
        try:
            with client() as c:
                return _ok(c.set_phases(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/phases/reset")
    def proxy_reset_phases():
        try:
            with client() as c:
                return _ok(c.reset_phases())
        except Exception as exc:
            return _err(exc)

    @app.get("/api/proxy/mqtt")
    def proxy_get_mqtt():
        try:
            with client() as c:
                return _ok(c.get_mqtt())
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/mqtt")
    def proxy_set_mqtt():
        try:
            with client() as c:
                return _ok(c.set_mqtt(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/grow")
    def proxy_grow():
        body = request.get_json(force=True, silent=True) or {}
        action = body.get("action")
        try:
            with client() as c:
                if action == "stop":
                    return _ok(c.grow_stop())
                if action == "set_day":
                    return _ok(c.grow_set_day(int(body["day"])))
                if action == "start":
                    return _ok(
                        c.grow_start(
                            total_days=int(body.get("total_days", 90)),
                            start_epoch=body.get("start_epoch"),
                            seed=body.get("seed"),
                            start_day=int(body.get("start_day", 1)),
                        )
                    )
                return jsonify({"error": "unknown action"}), 400
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/drying")
    def proxy_drying():
        body = request.get_json(force=True, silent=True) or {}
        action = body.get("action")
        try:
            with client() as c:
                if action == "start":
                    return _ok(c.drying_start())
                if action == "stop":
                    return _ok(c.drying_stop())
                return jsonify({"error": "unknown action"}), 400
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/notify")
    def proxy_notify():
        body = request.get_json(force=True, silent=True) or {}
        try:
            with client() as c:
                return _ok(
                    c.set_notify(
                        enabled=bool(body.get("enabled")),
                        phone=str(body.get("phone") or ""),
                        apikey=body.get("apikey"),
                    )
                )
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/app")
    def proxy_app_name():
        body = request.get_json(force=True, silent=True) or {}
        try:
            with client() as c:
                return _ok(c.set_device_name(str(body.get("name") or "")))
        except Exception as exc:
            return _err(exc)

    # Avoid double-start under Flask reloader parent process.
    if start_controller and (
        os.environ.get("WERKZEUG_RUN_MAIN") == "true" or os.environ.get("FLASK_DEBUG", "0") != "1"
    ):
        controller.start()
        atexit.register(controller.stop)

    return app


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "5050"))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    app = create_app(start_controller=True)
    print(f"Lisa Pro UI → device {app.config['DEVICE_URL']}")
    print(f"Open http://127.0.0.1:{port}")
    app.run(host=host, port=port, debug=debug)
