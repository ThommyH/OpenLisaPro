"""Flask control UI that proxies the Lisa Pro device API and runs local fan PID."""

from __future__ import annotations

import atexit
import csv
import io
import json
import math
import os
import re
import socket
import threading
import time
from copy import deepcopy
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

from lisa_pro_ui.client import DEFAULT_BASE_URL, LisaProClient, LisaProError
from lisa_pro_ui.config_store import ConfigStore
from lisa_pro_ui.controller import FanPidController
from lisa_pro_ui.telemetry import TelemetryStore

PACKAGE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = Path(os.environ.get("LISA_PRO_DATA", PACKAGE_DIR.parent.parent / "data"))
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


def _advertised_node_url(remote_url: str) -> str:
    """Build a LAN URL using the interface that reaches the peer/device."""
    configured = os.environ.get("LISA_PRO_HA_PUBLIC_URL", "").strip().rstrip("/")
    if configured:
        return configured
    port = int(os.environ.get("PORT", "5050"))
    parsed = urlsplit(remote_url)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((parsed.hostname or "127.0.0.1", parsed.port or (443 if parsed.scheme == "https" else 80)))
            address = probe.getsockname()[0]
    except OSError:
        address = "127.0.0.1"
    return f"{parsed.scheme or 'http'}://{address}:{port}"


def _valid_node_url(value: object) -> str | None:
    url = str(value or "").strip().rstrip("/")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return None
    return url


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
    ha_role = os.environ.get("LISA_PRO_HA_ROLE", "primary").strip().lower()
    if ha_role not in {"primary", "replica"}:
        raise ValueError("LISA_PRO_HA_ROLE must be 'primary' or 'replica'")
    app.config["HA_ROLE"] = ha_role
    app.config["HA_NODE_NAME"] = os.environ.get("LISA_PRO_HA_NAME", "OpenLisaPro")
    app.config["HA_MASTER_URL"] = os.environ.get("LISA_PRO_HA_MASTER_URL", "").rstrip("/")
    app.config["HA_NODE_URL"] = _advertised_node_url(app.config["HA_MASTER_URL"] or app.config["DEVICE_URL"])
    data_path = Path(data_dir or DEFAULT_DATA_DIR)
    store = ConfigStore(data_path / "control.json")
    telemetry = TelemetryStore(data_path / "openlisapro.sqlite3")
    telemetry.migrate_legacy_files(
        history_path=data_path / "controller-history.jsonl",
        flow_events_path=data_path / "flow-events.json",
    )
    flow_events_lock = threading.RLock()
    phase_write_lock = threading.RLock()
    controller = FanPidController(
        device_url=app.config["DEVICE_URL"],
        store=store,
        telemetry_store=telemetry,
        phase_write_lock=phase_write_lock,
    )
    app.config["CONTROL_STORE"] = store
    app.config["FAN_CONTROLLER"] = controller
    app.config["TELEMETRY_STORE"] = telemetry
    update_cache = {"checked_at": 0.0, "latest_version": None, "error": None}
    update_cache_lock = threading.Lock()
    ha_state = {"role": "primary" if ha_role == "primary" else "replica", "master_url": app.config["HA_MASTER_URL"], "master_healthy": ha_role == "primary", "last_check_at": None, "replica_last_seen": None, "replica_url": None, "replica_name": None}
    ha_lock = threading.RLock()
    ha_stop = threading.Event()
    ha_thread = None

    def ha_snapshot():
        with ha_lock:
            return deepcopy(ha_state)

    def set_ha_role(role):
        with ha_lock:
            changed = ha_state["role"] != role
            ha_state["role"] = role
            ha_state["master_healthy"] = role == "replica" if ha_role == "replica" else True
        if changed:
            if role == "primary":
                controller.start()
            else:
                controller.stop()
        return changed

    def apply_master_config_to_device() -> None:
        """Reapply the returned primary's settings after a promoted replica demotes."""
        cfg = store.get()
        try:
            if cfg.get("manual_mode"):
                controller._last_device_settings_sync_at = None
                controller._tick(cfg)
            else:
                controller.sync_configured_settings(
                    [], cfg, include_fan_limits=not bool(cfg.get("enabled"))
                )
        except Exception as exc:  # noqa: BLE001 — keep replica demoted; primary remains authoritative
            app.logger.error("Could not reapply primary settings to growbox after failback: %s", exc)

    def check_master():
        master_url = app.config["HA_MASTER_URL"]
        if not master_url:
            app.logger.error("Replica mode requires LISA_PRO_HA_MASTER_URL")
            return False
        healthy = False
        try:
            req = Request(f"{master_url}/api/ha/snapshot", headers={"Accept": "application/json"})
            with urlopen(req, timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("role") == "primary" and isinstance(payload.get("config"), dict):
                store.replace(payload["config"])
                healthy = True
                heartbeat_body = {"node_name": app.config["HA_NODE_NAME"], "node_url": app.config["HA_NODE_URL"]}
                heartbeat = Request(f"{master_url}/api/ha/heartbeat", data=json.dumps(heartbeat_body).encode(), headers={"Content-Type": "application/json"}, method="POST")
                with urlopen(heartbeat, timeout=3):
                    pass
        except Exception as exc:  # noqa: BLE001 — fail over when the master cannot be reached
            app.logger.warning("HA master check failed: %s", exc)
            healthy = False
        with ha_lock:
            ha_state["last_check_at"] = time.time()
            ha_state["master_healthy"] = healthy
        previous_role = ha_snapshot()["role"]
        changed = set_ha_role("replica" if healthy else "primary")
        if healthy and previous_role == "primary" and changed:
            apply_master_config_to_device()
        return healthy

    def monitor_master():
        if not app.config["HA_MASTER_URL"]:
            check_master()
            return
        while not ha_stop.wait(5.0):
            check_master()

    app.config["HA_CHECK_MASTER"] = check_master

    def client() -> LisaProClient:
        return LisaProClient(app.config["DEVICE_URL"])

    @app.get("/")
    def index():
        return render_template("index.html", device_url=app.config["DEVICE_URL"])

    @app.get("/api/meta")
    def meta():
        return jsonify({"device_url": app.config["DEVICE_URL"], "ha": ha_snapshot(), "node_name": app.config["HA_NODE_NAME"]})

    @app.get("/api/ha/snapshot")
    def ha_snapshot_route():
        if ha_role != "primary":
            return jsonify({"error": "This node is not the configured primary."}), 409
        return jsonify({"role": "primary", "node_name": app.config["HA_NODE_NAME"], "config": store.get()})

    @app.get("/api/ha")
    def ha_status():
        status = ha_snapshot()
        seen = status.get("replica_last_seen")
        status["replica_healthy"] = bool(seen and time.time() - seen < 15)
        status.update(node_name=app.config["HA_NODE_NAME"], node_url=app.config["HA_NODE_URL"], configured_role=ha_role)
        return jsonify(status)

    @app.post("/api/ha/heartbeat")
    def ha_replica_heartbeat():
        if ha_role != "primary":
            return jsonify({"error": "This node is not the configured primary."}), 409
        body = request.get_json(silent=True) or {}
        with ha_lock:
            ha_state["replica_last_seen"] = time.time()
            if body.get("node_name"):
                ha_state["replica_name"] = str(body["node_name"])[:100]
            node_url = _valid_node_url(body.get("node_url"))
            if node_url:
                ha_state["replica_url"] = node_url
        return jsonify({"ok": True})

    @app.before_request
    def enforce_replica_read_only():
        if request.method == "POST" and request.path.startswith("/api/proxy/") and ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Device settings are read-only on a replica."}), 409
        return None

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

    @app.get("/api/control/flow-events")
    def control_flow_events_get():
        total = telemetry.flow_event_count()
        total_pages = max(1, (total + FLOW_EVENT_PAGE_SIZE - 1) // FLOW_EVENT_PAGE_SIZE)
        page = min(total_pages, max(1, request.args.get("page", default=1, type=int)))
        start = (page - 1) * FLOW_EVENT_PAGE_SIZE
        return jsonify({
            "events": telemetry.list_flow_events(limit=FLOW_EVENT_PAGE_SIZE, offset=start),
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
        if not isinstance(at, (int, float)) or isinstance(at, bool) or not math.isfinite(at) or not 0 < at <= now_ms + 300_000:
            return jsonify({"error": "Flow event timestamp must be a valid epoch in milliseconds and not in the future."}), 400
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
            telemetry.record_flow_event(event, store.get())
            total = telemetry.flow_event_count()
        return jsonify({
            "event": event,
            "total": total,
            "total_pages": max(1, (total + FLOW_EVENT_PAGE_SIZE - 1) // FLOW_EVENT_PAGE_SIZE),
        }), 201

    @app.get("/api/data/measurements")
    def data_measurements():
        page = max(1, request.args.get("page", default=1, type=int))
        page_size = min(1000, max(1, request.args.get("page_size", default=100, type=int)))
        total = telemetry.measurement_count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        return jsonify({
            "measurements": telemetry.list_measurements(limit=page_size, offset=(page - 1) * page_size),
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
        })

    @app.get("/api/data/export")
    def data_export():
        def epoch_parameter(name: str) -> float | None:
            raw = request.args.get(name)
            if raw is None:
                return None
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a Unix timestamp in seconds") from exc
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a non-negative Unix timestamp in seconds")
            return value

        try:
            start = epoch_parameter("from")
            end = epoch_parameter("to")
            if start is not None and end is not None and start > end:
                raise ValueError("from must be earlier than or equal to to")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        export_format = request.args.get("format", "jsonl").lower()
        if export_format not in {"jsonl", "csv"}:
            return jsonify({"error": "format must be jsonl or csv"}), 400

        records = telemetry.iter_measurements(start=start, end=end)
        if export_format == "jsonl":
            def jsonl_rows():
                for record in records:
                    yield json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"

            return Response(
                stream_with_context(jsonl_rows()),
                mimetype="application/x-ndjson",
                headers={"Content-Disposition": "attachment; filename=openlisapro-measurements.jsonl"},
            )

        def csv_rows():
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow([
                "timestamp_unix", "temp_inside_c", "humidity_inside_rh", "dew_point_inside_c",
                "temp_outside_c", "humidity_outside_rh", "dew_point_outside_c", "vpd_inside_kpa",
                "vpd_outside_kpa", "fan_actual_pct", "fan_command_pct", "phase_id", "mode",
                "controller_json", "device_json", "settings_json", "legacy",
            ])
            yield buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            for record in records:
                metrics = record["metrics"]
                writer.writerow([
                    record["t"], metrics["temp_inside_c"], metrics["humidity_inside_rh"],
                    metrics["dew_point_inside_c"], metrics["temp_outside_c"],
                    metrics["humidity_outside_rh"], metrics["dew_point_outside_c"],
                    metrics["vpd_inside_kpa"], metrics["vpd_outside_kpa"],
                    metrics["fan_actual_pct"], metrics["fan_command_pct"], metrics["phase_id"],
                    metrics["mode"], json.dumps(record["controller"], separators=(",", ":")),
                    json.dumps(record["device"], separators=(",", ":")),
                    json.dumps(record["settings"], separators=(",", ":")) if record["settings"] is not None else "",
                    record["legacy"],
                ])
                yield buffer.getvalue()
                buffer.seek(0)
                buffer.truncate(0)

        return Response(
            stream_with_context(csv_rows()),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=openlisapro-measurements.csv"},
        )

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
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Settings are read-only on a replica."}), 409
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
        device_settings = {
            "led",
            "fan_limits",
            "vpd_targets",
            "phase_schedules",
            "manual_mode",
            "manual_fan_pct",
            "manual_led_pct",
        }
        if device_settings.intersection(body):
            try:
                if "led" in body or cfg.get("manual_mode"):
                    controller.reset_temperature_led_override()
                    cfg = store.get()
                if cfg.get("manual_mode"):
                    # Apply saved device configuration and the exact manual
                    # fan/LED values immediately, without waiting for the loop.
                    controller._last_device_settings_sync_at = None
                    controller._tick(cfg)
                else:
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
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Settings are read-only on a replica."}), 409
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
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Settings are read-only on a replica."}), 409
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
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Settings are read-only on a replica."}), 409
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
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Device settings are read-only on a replica."}), 409
        try:
            with phase_write_lock:
                with client() as c:
                    return _ok(c.set_phases(request.get_json(force=True, silent=True) or {}))
        except Exception as exc:
            return _err(exc)

    @app.post("/api/proxy/phases/reset")
    def proxy_reset_phases():
        if ha_snapshot()["role"] == "replica":
            return jsonify({"error": "Device settings are read-only on a replica."}), 409
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
    if start_controller and ha_role == "replica":
        ha_thread = threading.Thread(target=monitor_master, name="ha-monitor", daemon=True)
        ha_thread.start()
        atexit.register(ha_stop.set)
        atexit.register(controller.stop)
    elif start_controller and (
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
