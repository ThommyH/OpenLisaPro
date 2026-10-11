"""Flask control UI that proxies the Lisa Pro device API and runs local fan PID."""

from __future__ import annotations

import atexit
import json
import math
import os
import re
import threading
import time
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request

from lisa_pro_ui.client import DEFAULT_BASE_URL, LisaProClient, LisaProError
from lisa_pro_ui.config_store import ConfigStore
from lisa_pro_ui.controller import FanPidController

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(os.environ.get("LISA_PRO_DATA", PACKAGE_DIR.parent.parent / "data"))
FLOW_EVENT_RETENTION_MS = 14 * 24 * 60 * 60 * 1000
FLOW_EVENT_PAGE_SIZE = 20


def _release_version_key(value: object) -> tuple[tuple[int, ...], int, str] | None:
    """Return a sortable key for numeric release tags such as v1.2.3."""
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)(?:[-+](.+))?", str(value or "").strip(), re.IGNORECASE)
    if not match:
        return None
    parts = tuple(int(part) for part in match.group(1).split("."))
    # Stable releases sort after prereleases with the same numeric version.
    padded_parts = (parts + (0,) * 8)[:8]
    suffix = match.group(2) or ""
    return padded_parts, int(not suffix), suffix.lower()


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
    flow_events_path = data_path / "flow-events.json"
    flow_events_lock = threading.RLock()
    phase_write_lock = threading.RLock()
    controller = FanPidController(
        device_url=app.config["DEVICE_URL"],
        store=store,
        history_path=data_path / "controller-history.jsonl",
        phase_write_lock=phase_write_lock,
    )
    app.config["CONTROL_STORE"] = store
    app.config["FAN_CONTROLLER"] = controller
    update_cache = {"checked_at": 0.0, "latest_version": None, "error": None}
    update_cache_lock = threading.Lock()

    def client() -> LisaProClient:
        return LisaProClient(app.config["DEVICE_URL"])

    @app.get("/")
    def index():
        return render_template("index.html", device_url=app.config["DEVICE_URL"])

    @app.get("/api/meta")
    def meta():
        return jsonify({"device_url": app.config["DEVICE_URL"]})

    @app.get("/api/version")
    def version_status():
        try:
            installed_version = package_version("lisa-pro-ui")
        except PackageNotFoundError:
            installed_version = "development"
        current_version = os.environ.get("APP_VERSION", installed_version)
        force = request.args.get("refresh") == "1"
        now = time.time()
        cache_ttl = 30 if update_cache["error"] else 300
        if force or now - update_cache["checked_at"] >= cache_ttl:
            with update_cache_lock:
                now = time.time()
                cache_ttl = 30 if update_cache["error"] else 300
                if force or now - update_cache["checked_at"] >= cache_ttl:
                    try:
                        req = Request(
                            "https://api.github.com/repos/ThommyH/OpenLisaPro/tags?per_page=100",
                            headers={"Accept": "application/vnd.github+json", "User-Agent": "OpenLisaPro"},
                        )
                        with urlopen(req, timeout=4) as response:
                            payload = json.loads(response.read().decode("utf-8"))
                        tags = [
                            str(item.get("name", ""))
                            for item in payload
                            if isinstance(item, dict) and _release_version_key(item.get("name")) is not None
                        ]
                        latest_version = max(tags, key=_release_version_key) if tags else None
                        update_cache.update(checked_at=now, latest_version=latest_version, error=None)
                    except Exception as exc:  # noqa: BLE001 — remote check must not affect control
                        update_cache.update(checked_at=now, latest_version=None, error=str(exc))
        latest_version = update_cache["latest_version"]
        is_latest = bool(
            latest_version
            and _release_version_key(current_version) is not None
            and _release_version_key(current_version) == _release_version_key(latest_version)
        )
        return jsonify(
            {
                "version": current_version,
                "latest_version": latest_version,
                "is_latest": is_latest,
                "checked_at": update_cache["checked_at"],
                "error": update_cache["error"],
                "latest_url": f"https://github.com/ThommyH/OpenLisaPro/tree/{latest_version}" if latest_version else "https://github.com/ThommyH/OpenLisaPro/tags",
            }
        )

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
        limit = request.args.get("limit", default=0, type=int)
        return jsonify({"history": controller.history(limit=max(0, limit))})

    def read_flow_events() -> list[dict]:
        with flow_events_lock:
            try:
                payload = json.loads(flow_events_path.read_text(encoding="utf-8"))
                stored = payload.get("events", []) if isinstance(payload, dict) else []
                if not isinstance(stored, list):
                    stored = []
            except FileNotFoundError:
                stored = []
            except (OSError, json.JSONDecodeError) as exc:
                app.logger.warning("Could not read flow event history: %s", exc)
                stored = []

            cutoff = time.time() * 1000 - FLOW_EVENT_RETENTION_MS
            events = [
                event for event in stored
                if isinstance(event, dict)
                and isinstance(event.get("at"), (int, float))
                and cutoff <= event["at"] <= time.time() * 1000 + 300_000
            ]
            events.sort(key=lambda event: event["at"], reverse=True)
            if events != stored:
                write_flow_events(events)
            return events

    def write_flow_events(events: list[dict]) -> None:
        flow_events_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = flow_events_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"schema_version": 1, "events": events}, indent=2) + "\n", encoding="utf-8")
        temporary.replace(flow_events_path)

    @app.get("/api/control/flow-events")
    def control_flow_events_get():
        events = read_flow_events()
        total = len(events)
        total_pages = max(1, (total + FLOW_EVENT_PAGE_SIZE - 1) // FLOW_EVENT_PAGE_SIZE)
        page = min(total_pages, max(1, request.args.get("page", default=1, type=int)))
        start = (page - 1) * FLOW_EVENT_PAGE_SIZE
        return jsonify({
            "events": events[start:start + FLOW_EVENT_PAGE_SIZE],
            "page": page,
            "page_size": FLOW_EVENT_PAGE_SIZE,
            "total": total,
            "total_pages": total_pages,
        })

    @app.post("/api/control/flow-events")
    def control_flow_events_post():
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "A flow event object is required."}), 400
        at = body.get("at")
        now_ms = time.time() * 1000
        if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at) or not now_ms - FLOW_EVENT_RETENTION_MS <= at <= now_ms + 300_000:
            return jsonify({"error": "Flow event timestamp must be within the last 14 days."}), 400
        title = str(body.get("title") or "").strip()[:200]
        signature = str(body.get("signature") or "")[:1000]
        steps = body.get("steps")
        branches = body.get("branches")
        if not title or not signature or not isinstance(steps, list) or not isinstance(branches, list):
            return jsonify({"error": "Flow event title, signature, steps, and branches are required."}), 400
        event = {
            "at": int(at),
            "title": title,
            "steps": [str(item)[:100] for item in steps[:20]],
            "branches": [str(item)[:100] for item in branches[:20]],
            "signature": signature,
        }
        fan_change = body.get("fan_change")
        if isinstance(fan_change, dict):
            values = [fan_change.get(key) for key in ("from_pct", "to_pct", "delta_pct")]
            if all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) for value in values):
                temperature_boost = fan_change.get("temperature_boost_pct", 0)
                command_pct = fan_change.get("command_pct")
                event["fan_change"] = {
                    "from_pct": round(max(0.0, min(100.0, float(values[0]))), 2),
                    "to_pct": round(max(0.0, min(100.0, float(values[1]))), 2),
                    "delta_pct": round(max(-100.0, min(100.0, float(values[2]))), 2),
                    "temperature_boost_pct": round(float(temperature_boost), 2)
                    if isinstance(temperature_boost, (int, float)) and not isinstance(temperature_boost, bool) and math.isfinite(temperature_boost) else 0,
                    "command_pct": round(max(0.0, min(100.0, float(command_pct))), 2)
                    if isinstance(command_pct, (int, float)) and not isinstance(command_pct, bool) and math.isfinite(command_pct) else None,
                }
        with flow_events_lock:
            events = read_flow_events()
            events.insert(0, event)
            write_flow_events(events)
            total = len(events)
        return jsonify({
            "event": event,
            "total": total,
            "total_pages": max(1, (total + FLOW_EVENT_PAGE_SIZE - 1) // FLOW_EVENT_PAGE_SIZE),
        }), 201

    @app.post("/api/proxy/silent/toggle")
    def proxy_toggle_silent():
        try:
            with client() as c:
                settings = c.get_silent()
                enabled = not bool(settings.get("enabled"))
                settings["enabled"] = enabled
                updated = c.set_silent(settings)
            return _ok({**updated, "enabled": enabled})
        except Exception as exc:
            return _err(exc)

    led_status_pct = 0.0

    def _apply_saved_settings_to_device(cfg: dict, pct: float | None = None) -> None:
        """Apply locally saved phase settings while preserving device-only fields."""
        nonlocal led_status_pct
        controller.sync_configured_settings([], cfg)
        if pct is not None:
            led_status_pct = pct

    def _commanded_led_pct(led_map) -> float:
        """Return the max commanded LED percentage across all phases."""
        values = []
        for modes in (led_map or {}).values():
            if isinstance(modes, dict):
                values.extend(float(v) for v in modes.values() if str(v).strip())
        return max(values, default=0.0)

    def _validate_phase_schedules(schedule_map) -> None:
        if schedule_map is None:
            return
        if not isinstance(schedule_map, dict):
            raise ValueError("phase_schedules must be an object")
        for phase_id, schedule in schedule_map.items():
            if not isinstance(schedule, dict):
                raise ValueError(f"Invalid schedule for phase {phase_id}")
            for field in ("on", "off"):
                if field not in schedule:
                    continue
                value = str(schedule[field])
                parts = value.split(":")
                if len(parts) != 2 or any(len(part) != 2 or not part.isdigit() for part in parts):
                    raise ValueError(f"Invalid light {field} time: {value}")
                hour, minute = map(int, parts)
                if hour > 23 or minute > 59:
                    raise ValueError(f"Invalid light {field} time: {value}")

    @app.post("/api/control")
    def control_post():
        body = request.get_json(force=True, silent=True) or {}
        try:
            _validate_phase_schedules(body.get("phase_schedules"))
        except Exception as exc:
            return _err(exc, 400)
        try:
            cfg = store.update(body)
        except Exception as exc:
            return _err(exc, 400)
        device_error = None
        device_settings = {"led", "fan_limits", "vpd_targets", "phase_schedules"}
        if device_settings.intersection(body):
            try:
                if "led" in body:
                    controller.reset_temperature_led_override()
                    cfg = store.get()
                pct = _commanded_led_pct(cfg.get("led")) if "led" in body else None
                _apply_saved_settings_to_device(cfg, pct)
            except Exception as exc:  # noqa: BLE001 — local save should still succeed
                device_error = str(exc)
        payload = {
            "ok": True,
            "config": cfg,
            "state": controller.snapshot()["state"],
            "led_status_pct": round(led_status_pct, 2),
        }
        if device_error:
            payload["device_error"] = device_error
        return jsonify(payload)

    @app.post("/api/control/reset")
    def control_reset():
        nonlocal led_status_pct
        try:
            with phase_write_lock:
                with client() as c:
                    c.reset_phases()
                    phases = c.get_phases().get("phases") or []
            cfg = store.reset()
            cfg = store.ensure_fan_limits_from_phases(phases)
            _apply_saved_settings_to_device(cfg, 0.0)
            led_status_pct = 0.0
            return jsonify(
                {
                    "ok": True,
                    "config": cfg,
                    "phases": phases,
                    "state": controller.snapshot()["state"],
                    "led_status_pct": led_status_pct,
                }
            )
        except Exception as exc:
            return _err(exc)

    @app.post("/api/control/reset-stage")
    def control_reset_stage():
        body = request.get_json(force=True, silent=True) or {}
        try:
            phase_id = int(body.get("phase_id"))
        except (TypeError, ValueError):
            return _err(ValueError("phase_id must be an integer"), 400)
        try:
            with phase_write_lock:
                with client() as c:
                    current_phases = c.get_phases().get("phases") or []
                    if not any(int(p.get("id", -1)) == phase_id for p in current_phases):
                        return _err(ValueError(f"Unknown phase id: {phase_id}"), 404)
                    c.reset_phases()
                    factory_phases = c.get_phases().get("phases") or []
                    factory_phase = next(
                        (p for p in factory_phases if int(p.get("id", -1)) == phase_id), None
                    )
                    if factory_phase is None:
                        raise ValueError(f"Factory defaults do not contain phase id {phase_id}")
                    phases = [
                        deepcopy(factory_phase)
                        if int(p.get("id", -1)) == phase_id
                        else deepcopy(p)
                        for p in current_phases
                    ]
                    c.set_phases({"phases": phases})
            cfg = store.reset_stage_from_phase(phase_id, factory_phase)
            controller.reset_temperature_led_override(phase_id)
            return jsonify(
                {
                    "ok": True,
                    "phase": factory_phase,
                    "phases": phases,
                    "config": cfg,
                    "state": controller.snapshot()["state"],
                }
            )
        except Exception as exc:
            return _err(exc)

    @app.post("/api/control/seed-fans")
    def control_seed_fans():
        """Pull current device phase fan/LED settings into local overrides (if missing)."""
        try:
            with client() as c:
                phases = c.get_phases().get("phases") or []
            cfg = store.ensure_fan_limits_from_phases(phases)
            _apply_saved_settings_to_device(cfg, _commanded_led_pct(cfg.get("led")))
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
            with phase_write_lock:
                with client() as c:
                    return _ok(c.set_phases(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/phases/reset")
    def proxy_reset_phases():
        try:
            with phase_write_lock:
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
