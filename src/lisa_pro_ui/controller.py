"""Background VPD → fan PID that drives the device via fan_min=fan_max."""

from __future__ import annotations

import logging
import json
import threading
import time
from collections import deque
from copy import deepcopy
from datetime import datetime, time as datetime_time
from pathlib import Path
from typing import Any, Callable, Optional

from lisa_pro_ui.client import LisaProClient, LisaProError
from lisa_pro_ui.config_store import MODES, ConfigStore
from lisa_pro_ui.pid import PIDController
from lisa_pro_ui.telemetry import TelemetryStore
from lisa_pro_ui.vpd import dew_point_c, vpd_from_dew_point_kpa, vpd_kpa

log = logging.getLogger(__name__)

HISTORY_RETENTION_S = 2 * 24 * 60 * 60
HISTORY_MAX = HISTORY_RETENTION_S // 2  # Supports the 2s minimum controller interval.
HISTORY_SNAPSHOT_LIMIT = 360
TEMPERATURE_RESPONSE_HOLD_S = 90.0
TEMPERATURE_LED_REDUCTION_FACTOR = 0.5
TEMPERATURE_LED_MIN_PCT = 20.0
TEMPERATURE_LED_STEP_PCT = 10.0
TEMPERATURE_RECOVERY_MARGIN_C = 0.5
SOFT_TEMPERATURE_OBSERVATION_INTERVAL_S = 60.0
SOFT_TEMPERATURE_RESPONSE_THRESHOLD_C = 0.1
SOFT_TEMPERATURE_BOOST_STEP_PCT = 10.0
SOFT_TEMPERATURE_INITIAL_BOOST_PCT = 5.0
SOFT_TEMPERATURE_MIN_BOOST_PCT = 5.0
PHASE_SETTINGS_SYNC_INTERVAL_S = 60.0

PHASE_NAME_HINTS = (
    ("flower", ("flower", "blüte", "bluete", "blute", "bloom")),
    ("vegetative", ("vegetative", "veg", "wachstum")),
    ("seedling", ("seedling", "keim", "clone", "propagat")),
)


class FanPidController:
    def __init__(
        self,
        *,
        device_url: str,
        store: ConfigStore,
        history_path: Optional[Path] = None,
        telemetry_store: Optional[TelemetryStore] = None,
        client_factory: Optional[Callable[[], LisaProClient]] = None,
        phase_write_lock: Optional[threading.RLock] = None,
    ) -> None:
        self.device_url = device_url.rstrip("/")
        self.store = store
        self._history_path = Path(history_path) if history_path is not None else None
        self._telemetry_store = telemetry_store
        self._client_factory = client_factory or (lambda: LisaProClient(self.device_url))
        self._phase_write_lock = phase_write_lock or threading.RLock()
        self._lock = threading.RLock()
        self._tick_lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._pid = PIDController(output_min=-100.0, output_max=100.0)
        self._history: deque[dict[str, Any]] = deque(maxlen=HISTORY_MAX)
        self._last_history_prune = 0.0
        self._load_history()
        self._state: dict[str, Any] = {
            "running": False,
            "enabled": False,
            "manual_mode": False,
            "manual_fan_pct": 40.0,
            "manual_led_pct": 50.0,
            "last_error": None,
            "last_tick_at": None,
            "mode": None,
            "phase_id": None,
            "phase_name": None,
            "vpd_inside": None,
            "vpd_outside": None,
            "vpd_target": None,
            "vpd_target_raw": None,
            "temperature_active": False,
            "temperature_limit_c": None,
            "temperature_soft_limits": None,
            "temperature_soft_triggered": False,
            "temperature_soft_active": False,
            "temperature_adjustment_pct": 0.0,
            "temperature_inside_c": None,
            "temperature_outside_c": None,
            "temperature_warning": None,
            "led_override_pct": None,
            "vpd_reachable": True,
            "fan_min_limit": None,
            "fan_max_limit": None,
            "fan_vpd_request": None,
            "fan_command": None,
            "fan_ramp_target": None,
            "fan_actual": None,
            "pid_error": None,
            "pid_integral": None,
            "pid_output": None,
            "throttling": False,
            "reason": "idle",
        }
        self._cmd_speed: Optional[float] = None
        self._last_sent: Optional[tuple[int, str, float]] = None
        self._last_phase_snapshot: Optional[list[dict[str, Any]]] = None
        self._unreachable_since: Optional[float] = None
        self._last_throttle_at: Optional[float] = None
        self._prev_vpd: Optional[float] = None
        self._active_key: Optional[tuple[int, str]] = None
        self._temp_fan_max_since: Optional[float] = None
        self._last_temp_led_at: Optional[float] = None
        self._temp_led_pct: Optional[float] = None
        self._temp_led_key: Optional[tuple[int, str]] = None
        self._last_device_settings_sync_at: Optional[float] = None
        self._soft_temperature_key: Optional[tuple[int, str, float, float, float]] = None
        self._soft_temperature_boost_pct = 0.0
        self._soft_temperature_observed_at: Optional[float] = None
        self._soft_temperature_observed_c: Optional[float] = None
        self._manual_mode_active: Optional[bool] = None

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="fan-pid", daemon=True)
            self._thread.start()
            self._state["running"] = True

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._state["running"] = False

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": deepcopy(self._state),
                "config": self.store.get(),
                "history": list(self._history)[-HISTORY_SNAPSHOT_LIMIT:],
            }

    def history(self, *, limit: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._history)
        cutoff = time.time() - HISTORY_RETENTION_S
        items = [item for item in items if float(item.get("t", 0)) >= cutoff]
        if limit > 0:
            return items[-limit:]
        return items

    def _loop(self) -> None:
        while not self._stop.is_set():
            cfg = self.store.get()
            interval = 5.0
            try:
                interval = self._tick(cfg)
            except Exception as exc:  # noqa: BLE001 — keep loop alive
                log.exception("PID tick failed")
                with self._lock:
                    self._state["last_error"] = str(exc)
                    self._state["reason"] = "error"
            self._stop.wait(max(2.0, float(interval)))

    def _tick(self, cfg: dict[str, Any]) -> float:
        with self._tick_lock:
            return self._tick_once(cfg)

    def _tick_once(self, cfg: dict[str, Any]) -> float:
        now = time.time()

        with self._client_factory() as client:
            status = client.status()
        cfg = self.store.get()
        manual_mode = bool(cfg.get("manual_mode"))
        phases: list[dict[str, Any]] = []

        phases = self._ensure_night_leds_off(phases, enforce=not manual_mode)

        self.store.ensure_fan_limits_from_phases(phases)
        cfg = self.store.get()
        sync_now = time.monotonic()
        if (
            self._last_device_settings_sync_at is None
            or sync_now - self._last_device_settings_sync_at >= PHASE_SETTINGS_SYNC_INTERVAL_S
        ):
            phases = self.sync_configured_settings(
                phases,
                cfg,
                include_fan_limits=not bool(cfg.get("enabled")) and not manual_mode,
            )
            self._last_device_settings_sync_at = sync_now

        phase, phase_id = self._resolve_phase(status, phases)
        mode = self._resolve_mode(status, phase)
        if self._manual_mode_active != manual_mode:
            self._manual_mode_active = manual_mode
            self._last_sent = None
            self._cmd_speed = None
            self._active_key = None
            self._pid.reset()
            self._unreachable_since = None
            self._last_throttle_at = None
        enabled = bool(cfg.get("enabled")) and not manual_mode
        led_mode = mode if mode in ("day", "day_silent") else "night"
        saved_led_override = cfg.get("temperature_led_override")
        current_temp_key = (int(phase_id), led_mode)
        if isinstance(saved_led_override, dict):
            try:
                saved_key = (int(saved_led_override["phase_id"]), str(saved_led_override["mode"]))
                saved_base = float(saved_led_override["base_pct"])
            except (KeyError, TypeError, ValueError):
                saved_key = None
                saved_base = None
            configured_base = self._temperature_led_base(cfg, phases, phase_id, led_mode)
            if (
                saved_key == current_temp_key
                and configured_base is not None
                and saved_base is not None
                and abs(saved_base - configured_base) >= 0.01
            ):
                # The user changed the configured LED level while a temperature
                # override was active. Restore that new level on the device before
                # dropping the persisted override; otherwise the stale, higher
                # temporary level survives an application restart.
                self._write_temperature_led(phases, phase_id, led_mode, configured_base)
                self._clear_temperature_led_override()
                saved_led_override = None
        if self._temp_led_key is None and isinstance(saved_led_override, dict):
            try:
                self._temp_led_key = (int(saved_led_override["phase_id"]), str(saved_led_override["mode"]))
                self._temp_led_pct = float(saved_led_override["current_pct"])
                self._last_temp_led_at = float(saved_led_override["last_at"])
            except (KeyError, TypeError, ValueError):
                self._temp_led_key = None
                self._temp_led_pct = None
                self._last_temp_led_at = None
        temperature_limit = self._temperature_limit(cfg, phase_id)
        temperature_soft_limits = self._temperature_soft_limits(cfg, phase_id)
        temperature_inside = self._safe_float(status.get("temp_c"))
        temperature_outside = self._safe_float(status.get("temp_out_c"))
        temperature_active = (
            enabled
            and temperature_limit is not None
            and temperature_inside is not None
            and temperature_inside > temperature_limit
        )
        temperature_outside_limited = (
            temperature_active
            and temperature_outside is not None
            and temperature_outside >= temperature_limit
        )
        if self._temp_led_key is not None and self._temp_led_key != current_temp_key:
            self._restore_temperature_led(phases, cfg)
        if temperature_limit is None or temperature_inside is None:
            temperature_active = False
            self._restore_temperature_led(phases, cfg)
            self._temp_fan_max_since = None
            self._last_temp_led_at = None
        if (
            temperature_limit is not None
            and temperature_inside is not None
            and temperature_inside <= temperature_limit - TEMPERATURE_RECOVERY_MARGIN_C
        ):
            self._restore_temperature_led(phases, cfg)
            self._temp_fan_max_since = None
            self._last_temp_led_at = None
        pid_cfg = dict(cfg.get("pid") or {})
        if self._active_key is None or self._active_key[0] != int(phase_id):
            self._pid.reset()
        self._pid.configure(
            kp=float(pid_cfg.get("kp", 35)),
            ki=float(pid_cfg.get("ki", 0.12)),
            kd=float(pid_cfg.get("kd", 6)),
            integral_limit=float(pid_cfg.get("integral_limit", 200)),
        )
        interval = float(pid_cfg.get("interval_s", 5.0))
        limits = self._fan_limits(cfg, phase_id, mode, phase)
        fan_min = float(limits["fan_min"])
        fan_max = float(limits["fan_max"])

        vpd_inside = vpd_from_dew_point_kpa(status.get("temp_c"), status.get("dew_c"))
        if vpd_inside is None:
            vpd_inside = status.get("vpd_kpa")
        if vpd_inside is None:
            vpd_inside = vpd_kpa(status.get("temp_c"), status.get("humi_rh"))
        vpd_outside = vpd_kpa(status.get("temp_out_c"), status.get("humi_out_rh"))
        outside_dew_point = status.get("dew_out_c")
        if outside_dew_point is None:
            outside_dew_point = dew_point_c(status.get("temp_out_c"), status.get("humi_out_rh"))
        ventilation_vpd_limit = vpd_from_dew_point_kpa(status.get("temp_c"), outside_dew_point)

        vpd_min, vpd_max = self._vpd_targets(cfg, phase_id, phase, mode)
        # Prefer the lower edge of the configured VPD band, just as fan control
        # prefers its minimum; the controller raises ventilation only as needed.
        target_raw = vpd_min
        # Outside dew point represents its moisture content. Evaluate that
        # moisture at the inside temperature to estimate the highest VPD that
        # ventilation alone can produce; outdoor VPD itself is temperature-
        # dependent and cannot be compared directly with the inside target.
        reachable = ventilation_vpd_limit is None or target_raw <= ventilation_vpd_limit
        target = min(target_raw, ventilation_vpd_limit) if ventilation_vpd_limit is not None else target_raw
        reason = "tracking" if reachable else "target_limited_by_outside_dew_point"

        fan_actual = status.get("fan_pct")

        if manual_mode:
            manual_fan_pct = max(0.0, min(100.0, float(cfg.get("manual_fan_pct", 40.0))))
            manual_led_pct = max(0.0, min(100.0, float(cfg.get("manual_led_pct", 50.0))))
            self._apply_manual_settings(phases, phase_id, mode, manual_fan_pct, manual_led_pct)
            if self._temp_led_key is not None or cfg.get("temperature_led_override") is not None:
                self._clear_temperature_led_override()
            self._reset_soft_temperature_boost()
            self._temp_fan_max_since = None
            self._last_temp_led_at = None
            self._cmd_speed = manual_fan_pct
            self._last_sent = (int(phase_id), mode, manual_fan_pct)
            self._active_key = (int(phase_id), mode)
            self._pid.reset()
            self._unreachable_since = None
            self._last_throttle_at = None
            with self._lock:
                self._state.update(
                    {
                        "enabled": False,
                        "manual_mode": True,
                        "manual_fan_pct": manual_fan_pct,
                        "manual_led_pct": manual_led_pct,
                        "last_tick_at": now,
                        "mode": mode,
                        "phase_id": phase_id,
                        "phase_name": (phase or {}).get("name"),
                        "vpd_inside": None if vpd_inside is None else round(float(vpd_inside), 3),
                        "vpd_outside": vpd_outside,
                        "vpd_target": target,
                        "vpd_target_raw": target_raw,
                        "temperature_active": False,
                        "temperature_limit_c": temperature_limit,
                        "temperature_soft_limits": temperature_soft_limits,
                        "temperature_soft_triggered": False,
                        "temperature_soft_active": False,
                        "temperature_adjustment_pct": 0.0,
                        "temperature_inside_c": temperature_inside,
                        "temperature_outside_c": temperature_outside,
                        "temperature_warning": None,
                        "led_override_pct": None,
                        "vpd_reachable": reachable,
                        "fan_min_limit": manual_fan_pct,
                        "fan_max_limit": manual_fan_pct,
                        "fan_vpd_request": None,
                        "fan_command": manual_fan_pct,
                        "fan_ramp_target": manual_fan_pct,
                        "fan_actual": fan_actual,
                        "pid_error": None,
                        "pid_deadband_kpa": float(pid_cfg.get("deadband_kpa", 0.03)),
                        "pid_integral": 0.0,
                        "pid_output": None,
                        "throttling": False,
                        "reason": "manual",
                        "last_error": None,
                    }
                )
                self._append_history(now, device_measurements=status)
            return interval

        if not enabled:
            self._reset_soft_temperature_boost()
            # Restore configured min/max band so the device is not left at min=max.
            if self._last_sent is not None:
                try:
                    self._apply_fan_band(phases, phase_id, mode, fan_min, fan_max)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to restore fan band: %s", exc)
                self._last_sent = None
            self._restore_temperature_led(phases, cfg)
            self._temp_fan_max_since = None
            self._last_temp_led_at = None
            with self._lock:
                self._state.update(
                    {
                        "enabled": False,
                        "manual_mode": False,
                        "last_tick_at": now,
                        "mode": mode,
                        "phase_id": phase_id,
                        "phase_name": (phase or {}).get("name"),
                        "vpd_inside": vpd_inside,
                        "vpd_outside": vpd_outside,
                        "vpd_target": target,
                        "vpd_target_raw": target_raw,
                        "vpd_reachable": reachable,
                        "temperature_active": False,
                        "temperature_limit_c": temperature_limit,
                        "temperature_soft_limits": temperature_soft_limits,
                        "temperature_soft_triggered": False,
                        "temperature_soft_active": False,
                        "temperature_adjustment_pct": 0.0,
                        "temperature_inside_c": temperature_inside,
                        "temperature_outside_c": temperature_outside,
                        "temperature_warning": None,
                        "led_override_pct": None,
                        "fan_min_limit": fan_min,
                        "fan_max_limit": fan_max,
                        "fan_vpd_request": None,
                        "fan_command": None,
                        "fan_ramp_target": None,
                        "fan_actual": fan_actual,
                        "pid_error": None,
                        "pid_integral": self._pid.integral,
                        "pid_output": None,
                        "throttling": False,
                        "reason": "disabled",
                        "last_error": None,
                    }
                )
                self._append_history(now, device_measurements=status)
            self._cmd_speed = None
            self._active_key = None
            self._unreachable_since = None
            self._pid.reset()
            return interval

        if vpd_inside is None and not temperature_active:
            self._reset_soft_temperature_boost()
            with self._lock:
                self._state.update(
                    {
                        "enabled": True,
                        "manual_mode": False,
                        "last_tick_at": now,
                        "mode": mode,
                        "phase_id": phase_id,
                        "phase_name": (phase or {}).get("name"),
                        "vpd_inside": None,
                        "vpd_outside": vpd_outside,
                        "vpd_target": target,
                        "vpd_target_raw": target_raw,
                        "vpd_reachable": reachable,
                        "temperature_active": False,
                        "temperature_limit_c": temperature_limit,
                        "temperature_soft_limits": temperature_soft_limits,
                        "temperature_soft_triggered": False,
                        "temperature_soft_active": False,
                        "temperature_adjustment_pct": 0.0,
                        "temperature_inside_c": temperature_inside,
                        "temperature_outside_c": temperature_outside,
                        "temperature_warning": None,
                        "led_override_pct": None,
                        "fan_min_limit": fan_min,
                        "fan_max_limit": fan_max,
                        "fan_vpd_request": None,
                        "fan_ramp_target": None,
                        "fan_actual": fan_actual,
                        "reason": "no_inside_vpd",
                        "last_error": "Missing inside VPD / climate reading",
                    }
                )
                self._append_history(now, device_measurements=status)
            return interval

        deadband = float(pid_cfg.get("deadband_kpa", 0.03))
        error = target - float(vpd_inside) if vpd_inside is not None else 0.0
        pid_out = self._pid.update(error, now, deadband=deadband)
        # Positive error (need higher VPD) → higher exhaust within [fan_min, fan_max].
        desired = self._map_pid_to_fan(pid_out, fan_min, fan_max, error, deadband=deadband)
        vpd_fan_request = desired

        # Soft temperature control is a gradual feedback boost above the VPD
        # request. It can raise exhaust, but never lower the VPD request, and
        # still applies when VPD is outside its band. Hard maximum protection
        # below takes priority.
        soft_min = (temperature_soft_limits or {}).get("min_temp_c")
        soft_max = (temperature_soft_limits or {}).get("max_temp_c")
        soft_temperature_hot = (
            not temperature_active
            and temperature_inside is not None
            and soft_max is not None
            and temperature_inside > soft_max
        )
        temperature_adjustment = self._soft_temperature_boost(
            phase_id=phase_id,
            mode=mode,
            temperature_c=temperature_inside,
            soft_max_c=soft_max,
            hot=soft_temperature_hot,
            max_available_pct=max(0.0, fan_max - desired),
            max_stage_boost_pct=max(
                0.0,
                min(100.0, float((temperature_soft_limits or {}).get("max_boost_pct", 20.0))),
            ),
            weight_pct_per_c=max(
                0.0, min(20.0, float(pid_cfg.get("temperature_weight_pct_per_c", 15.0)))
            ),
            now=now,
        )
        desired = max(fan_min, min(fan_max, desired + temperature_adjustment))

        # Unreachable / stuck at ceiling → throttle down.
        throttling = False
        at_ceiling = desired >= fan_max - 0.5
        stuck = abs(error) >= float(pid_cfg.get("unreachable_error_kpa", 0.08))
        improving = (
            self._prev_vpd is not None
            and ((error > 0 and float(vpd_inside) > self._prev_vpd + 0.01) or (error < 0 and float(vpd_inside) < self._prev_vpd - 0.01))
        )
        if not temperature_active and not soft_temperature_hot and (not reachable or (at_ceiling and stuck and not improving)) and abs(error) > deadband:
            if self._unreachable_since is None:
                self._unreachable_since = now
            hold = float(pid_cfg.get("unreachable_hold_s", 90))
            if now - self._unreachable_since >= hold:
                throttling = True
                reason = "throttling_unreachable"
                step = float(pid_cfg.get("throttle_step_pct", 3))
                every = float(pid_cfg.get("throttle_interval_s", 30))
                if self._last_throttle_at is None or now - self._last_throttle_at >= every:
                    if self._cmd_speed is None:
                        self._cmd_speed = desired
                    self._cmd_speed = max(fan_min, self._cmd_speed - step)
                    self._last_throttle_at = now
                    desired = self._cmd_speed
                else:
                    desired = self._cmd_speed if self._cmd_speed is not None else desired
        else:
            self._unreachable_since = None
            self._last_throttle_at = None

        if temperature_active:
            # Heat protection takes priority over the VPD controller. Keep the
            # configured fan ceiling and ramp toward it without VPD backoff.
            desired = fan_max
            throttling = False
            reason = "temperature_limited_by_outside" if temperature_outside_limited else "over_temperature"
            self._unreachable_since = None
            self._last_throttle_at = None

        # Reset ramp anchor when phase/mode band changes.
        key = (int(phase_id), mode)
        if self._active_key != key:
            self._active_key = key
            self._cmd_speed = None
            self._last_sent = None
            self._unreachable_since = None
            self._last_throttle_at = None

        # Slow ramp of commanded min=max speed.
        ramp_per_min = float(pid_cfg.get("ramp_pct_per_min", 15.0))
        if self._cmd_speed is None:
            start = float(fan_actual) if fan_actual is not None else fan_min
            self._cmd_speed = max(fan_min, min(fan_max, start))
        max_step = max(0.1, ramp_per_min * (float(pid_cfg.get("interval_s", 5)) / 60.0))
        if temperature_active:
            # Temperature is a safety override, so do not let the normal fan
            # ramp delay cooling while the stage is above its configured limit.
            self._cmd_speed = fan_max
        elif not throttling:
            delta = desired - self._cmd_speed
            if abs(delta) <= max_step:
                self._cmd_speed = desired
            else:
                self._cmd_speed += max_step if delta > 0 else -max_step
                reason = "ramping"
        self._cmd_speed = max(fan_min, min(fan_max, self._cmd_speed))
        commanded = float(round(self._cmd_speed))

        # Push to device when speed or active mode/phase changes.
        need_send = (
            self._last_sent is None
            or self._last_sent[0] != int(phase_id)
            or self._last_sent[1] != mode
            or abs(commanded - self._last_sent[2]) >= 1
        )
        if need_send:
            self._apply_fan_command(phases, phase_id, mode, commanded)
            self._last_sent = (int(phase_id), mode, commanded)
            if reason == "tracking":
                reason = "applied"

        if temperature_active:
            at_fan_max = (
                (fan_actual is not None and float(fan_actual) >= fan_max - 0.5)
                or self._cmd_speed >= fan_max - 0.5
            )
            if temperature_outside_limited:
                self._reduce_temperature_led(phases, cfg, phase_id, led_mode, now=now, halve=True)
            elif at_fan_max:
                if self._temp_fan_max_since is None:
                    self._temp_fan_max_since = now
                elif now - self._temp_fan_max_since >= TEMPERATURE_RESPONSE_HOLD_S:
                    self._reduce_temperature_led(phases, cfg, phase_id, led_mode, now=now)
            else:
                self._temp_fan_max_since = None

        if vpd_inside is not None:
            self._prev_vpd = float(vpd_inside)
        with self._lock:
            self._state.update(
                {
                    "enabled": True,
                    "manual_mode": False,
                    "last_tick_at": now,
                    "mode": mode,
                    "phase_id": phase_id,
                    "phase_name": (phase or {}).get("name"),
                    "vpd_inside": None if vpd_inside is None else round(float(vpd_inside), 3),
                    "vpd_outside": None if vpd_outside is None else round(float(vpd_outside), 3),
                    "vpd_target": round(float(target), 3),
                    "vpd_target_raw": round(float(target_raw), 3),
                    "temperature_active": temperature_active,
                    "temperature_limit_c": temperature_limit,
                    "temperature_soft_limits": temperature_soft_limits,
                    "temperature_soft_triggered": soft_temperature_hot,
                    "temperature_soft_active": abs(temperature_adjustment) >= 0.01,
                    "temperature_adjustment_pct": round(temperature_adjustment, 2),
                    "temperature_inside_c": temperature_inside,
                    "temperature_outside_c": temperature_outside,
                    "temperature_warning": self._temperature_warning(
                        temperature_active,
                        temperature_outside_limited,
                        self._temp_led_pct is not None,
                        temperature_limit,
                        temperature_outside,
                    ),
                    "led_override_pct": self._temp_led_pct,
                    "vpd_reachable": reachable,
                    "fan_min_limit": fan_min,
                    "fan_max_limit": fan_max,
                    "fan_vpd_request": round(float(vpd_fan_request), 2),
                    "fan_command": commanded,
                    "fan_ramp_target": round(float(desired)),
                    "fan_actual": fan_actual,
                    "pid_error": None if vpd_inside is None else round(error, 4),
                    "pid_deadband_kpa": deadband,
                    "pid_integral": round(self._pid.integral, 4),
                    "pid_output": round(pid_out, 3),
                    "throttling": throttling,
                    "reason": reason,
                    "last_error": None,
                }
            )
            self._append_history(now, device_measurements=status)
        return interval

    @staticmethod
    def _map_pid_to_fan(
        pid_out: float,
        fan_min: float,
        fan_max: float,
        error: float,
        *,
        deadband: float = 0.0,
    ) -> float:
        """Start at the configured minimum and add exhaust only when VPD needs it.

        Positive PID output means inside VPD is below target, so more exhaust is
        requested. Zero or negative output stays at the minimum fan limit.
        """
        if error <= deadband:
            return fan_min
        span = max(0.0, fan_max - fan_min)
        if span <= 0:
            return fan_min
        return max(fan_min, min(fan_max, fan_min + max(0.0, pid_out)))

    def _apply_fan_command(
        self,
        phases: list[dict[str, Any]],
        phase_id: int,
        mode: str,
        speed: float,
    ) -> None:
        self._apply_fan_band(phases, phase_id, mode, speed, speed)

    def _ensure_night_leds_off(
        self, phases: list[dict[str, Any]], *, enforce: bool = True
    ) -> list[dict[str, Any]]:
        with self._phase_write_lock:
            with self._client_factory() as client:
                latest = client.get_phases().get("phases") or []
                if not enforce:
                    return latest
                updated = deepcopy(latest)
                changed = False
                for phase in updated:
                    settings = phase.get("settings") or {}
                    for mode in ("night", "night_silent"):
                        bucket = settings.get(mode)
                        if not isinstance(bucket, dict):
                            continue
                        if self._safe_float(bucket.get("led")) != 0.0:
                            bucket["led"] = 0.0
                            changed = True
                if changed:
                    client.set_phases({"phases": updated})
        if not changed:
            return latest
        self._last_phase_snapshot = updated
        return updated

    def _apply_manual_settings(
        self,
        phases: list[dict[str, Any]],
        phase_id: int,
        mode: str,
        fan_pct: float,
        led_pct: float,
    ) -> None:
        """Write direct fan and LED values without PID or temperature overrides."""
        fan_pct = max(0.0, min(100.0, float(fan_pct)))
        led_pct = max(0.0, min(100.0, float(led_pct)))
        with self._phase_write_lock:
            with self._client_factory() as client:
                latest = client.get_phases().get("phases") or []
                updated = deepcopy(latest)
                target_phase = next(
                    (item for item in updated if int(item.get("id", -1)) == int(phase_id)), None
                )
                if target_phase is None and updated:
                    target_phase = updated[min(int(phase_id), len(updated) - 1)]
                if target_phase is None:
                    raise LisaProError("No phases available to apply manual settings")
                settings = target_phase.setdefault("settings", {})
                mode_settings = settings.setdefault(mode, {})
                changed = False
                for field, value in (("fan_min", fan_pct), ("fan_max", fan_pct), ("led", led_pct)):
                    if self._safe_float(mode_settings.get(field)) != value:
                        mode_settings[field] = value
                        changed = True
                if changed:
                    client.set_phases({"phases": updated})
        phases[:] = deepcopy(updated)
        self._last_phase_snapshot = deepcopy(updated)

    def _apply_fan_band(
        self,
        phases: list[dict[str, Any]],
        phase_id: int,
        mode: str,
        fan_min: float,
        fan_max: float,
    ) -> None:
        with self._phase_write_lock:
            with self._client_factory() as client:
                latest = client.get_phases().get("phases") or []
                updated = deepcopy(latest)
                target_phase = next((p for p in updated if int(p.get("id", -1)) == int(phase_id)), None)
                if target_phase is None and updated:
                    target_phase = updated[min(int(phase_id), len(updated) - 1)]
                if target_phase is None:
                    raise LisaProError("No phases available to update fan speed")

                settings = target_phase.setdefault("settings", {})
                mode_settings = settings.setdefault(mode, {})
                mode_settings["fan_min"] = float(fan_min)
                mode_settings["fan_max"] = float(fan_max)
                client.set_phases({"phases": updated})
        phases[:] = deepcopy(updated)
        self._last_phase_snapshot = updated

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number == number and abs(number) != float("inf") else None

    @staticmethod
    def _temperature_limit(cfg: dict[str, Any], phase_id: int) -> Optional[float]:
        values = (cfg.get("temperature_limits") or {}).get(str(phase_id)) or {}
        return FanPidController._safe_float(values.get("max_temp_c"))

    @staticmethod
    def _temperature_soft_limits(cfg: dict[str, Any], phase_id: int) -> Optional[dict[str, Any]]:
        values = (cfg.get("temperature_soft_limits") or {}).get(str(phase_id)) or {}
        if not bool(values.get("enabled", True)):
            return None
        limits = {
            key: value
            for key in ("min_temp_c", "max_temp_c", "max_boost_pct")
            if (value := FanPidController._safe_float(values.get(key))) is not None
        }
        return limits or None

    def _soft_temperature_boost(
        self,
        *,
        phase_id: int,
        mode: str,
        temperature_c: Optional[float],
        soft_max_c: Optional[float],
        hot: bool,
        max_available_pct: float,
        max_stage_boost_pct: float,
        weight_pct_per_c: float,
        now: float,
    ) -> float:
        """Adjust the extra fan boost gradually from the observed temperature trend.

        Start with a small boost. Increase whenever the temperature is flat or
        rising; only reduce after an observation window confirms that it is
        falling. This keeps trying to bring temperature below the soft limit.
        """
        if not hot or temperature_c is None or soft_max_c is None:
            self._reset_soft_temperature_boost()
            return 0.0

        key = (
            int(phase_id),
            mode,
            float(soft_max_c),
            float(max_stage_boost_pct),
            float(weight_pct_per_c),
        )
        if self._soft_temperature_key != key:
            self._reset_soft_temperature_boost()
            self._soft_temperature_key = key

        target_boost = min(
            max(0.0, float(max_available_pct)),
            max(0.0, float(max_stage_boost_pct)),
            max(0.0, (float(temperature_c) - float(soft_max_c)) * float(weight_pct_per_c)),
        )
        if target_boost <= 0.0:
            self._reset_soft_temperature_boost()
            return 0.0

        if self._soft_temperature_observed_at is None:
            self._soft_temperature_boost_pct = min(
                SOFT_TEMPERATURE_INITIAL_BOOST_PCT, target_boost
            )
            self._soft_temperature_observed_at = now
            self._soft_temperature_observed_c = float(temperature_c)
        elif now - self._soft_temperature_observed_at >= SOFT_TEMPERATURE_OBSERVATION_INTERVAL_S:
            previous_temp = self._soft_temperature_observed_c
            trend = float(temperature_c) - float(previous_temp if previous_temp is not None else temperature_c)
            if trend >= -SOFT_TEMPERATURE_RESPONSE_THRESHOLD_C:
                self._soft_temperature_boost_pct = min(
                    target_boost,
                    self._soft_temperature_boost_pct + SOFT_TEMPERATURE_BOOST_STEP_PCT,
                )
            elif trend < -SOFT_TEMPERATURE_RESPONSE_THRESHOLD_C:
                self._soft_temperature_boost_pct = max(
                    min(SOFT_TEMPERATURE_MIN_BOOST_PCT, target_boost),
                    self._soft_temperature_boost_pct - SOFT_TEMPERATURE_BOOST_STEP_PCT,
                )
            self._soft_temperature_observed_at = now
            self._soft_temperature_observed_c = float(temperature_c)

        self._soft_temperature_boost_pct = min(self._soft_temperature_boost_pct, target_boost)
        return round(self._soft_temperature_boost_pct, 2)

    def _reset_soft_temperature_boost(self) -> None:
        self._soft_temperature_key = None
        self._soft_temperature_boost_pct = 0.0
        self._soft_temperature_observed_at = None
        self._soft_temperature_observed_c = None

    @staticmethod
    def _temperature_led_base(
        cfg: dict[str, Any], phases: list[dict[str, Any]], phase_id: int, led_mode: str
    ) -> Optional[float]:
        configured = (cfg.get("led") or {}).get(str(phase_id)) or {}
        value = configured.get(led_mode)
        if value is None:
            phase = next((p for p in phases if int(p.get("id", -1)) == int(phase_id)), None)
            value = (((phase or {}).get("settings") or {}).get(led_mode) or {}).get("led")
        return FanPidController._safe_float(value)

    def _write_temperature_led(
        self, phases: list[dict[str, Any]], phase_id: int, led_mode: str, pct: float
    ) -> None:
        with self._phase_write_lock:
            with self._client_factory() as client:
                latest = client.get_phases().get("phases") or []
                updated = deepcopy(latest)
                target = next((p for p in updated if int(p.get("id", -1)) == int(phase_id)), None)
                if target is None:
                    return
                settings = target.setdefault("settings", {})
                mode_settings = settings.setdefault(led_mode, {})
                value = max(0.0, min(100.0, float(pct)))
                if self._safe_float(mode_settings.get("led")) == value:
                    return
                mode_settings["led"] = value
                client.set_phases({"phases": updated})
        phases[:] = deepcopy(updated)
        self._last_phase_snapshot = updated

    def sync_configured_settings(
        self,
        phases: list[dict[str, Any]],
        cfg: dict[str, Any],
        *,
        include_fan_limits: bool = True,
    ) -> list[dict[str, Any]]:
        """Apply locally saved phase settings to the device, preserving unrelated fields."""
        led_map = cfg.get("led") or {}
        fan_map = cfg.get("fan_limits") or {}
        vpd_map = cfg.get("vpd_targets") or {}
        schedule_map = cfg.get("phase_schedules") or {}
        override = cfg.get("temperature_led_override")
        with self._phase_write_lock:
            with self._client_factory() as client:
                latest = client.get_phases().get("phases") or []
                updated = deepcopy(latest)
                changed = False
                for phase in updated:
                    phase_id = str(phase.get("id", 0))
                    led_modes = led_map.get(phase_id) or {}
                    fan_modes = fan_map.get(phase_id) or {}
                    vpd_modes = vpd_map.get(phase_id) or {}
                    settings = phase.setdefault("settings", {})
                    for mode in ("day", "day_silent", "night", "night_silent"):
                        bucket = settings.setdefault(mode, {})
                        fan_limits = fan_modes.get(mode)
                        # With PID enabled these device fields carry the live
                        # fan command during periodic sync. Explicit saves and
                        # startup still write the configured bounds.
                        if include_fan_limits and isinstance(fan_limits, dict):
                            for field in ("fan_min", "fan_max"):
                                value = self._safe_float(fan_limits.get(field))
                                if value is not None and self._safe_float(bucket.get(field)) != value:
                                    bucket[field] = value
                                    changed = True
                        vpd_targets = vpd_modes.get(mode)
                        if isinstance(vpd_targets, dict):
                            for field in ("vpd_min", "vpd_max"):
                                value = self._safe_float(vpd_targets.get(field))
                                if value is not None and self._safe_float(bucket.get(field)) != value:
                                    bucket[field] = value
                                    changed = True
                        if mode in led_modes:
                            desired = self._safe_float(led_modes[mode])
                            if desired is not None:
                                if (
                                    isinstance(override, dict)
                                    and str(override.get("phase_id")) == phase_id
                                    and str(override.get("mode")) == mode
                                ):
                                    override_pct = self._safe_float(override.get("current_pct"))
                                    if override_pct is not None:
                                        desired = min(desired, override_pct)
                                if self._safe_float(bucket.get("led")) != desired:
                                    bucket["led"] = desired
                                    changed = True

                    schedule = schedule_map.get(phase_id)
                    if isinstance(schedule, dict):
                        phase_schedule = phase.setdefault("schedule", {})
                        for field in ("on", "off"):
                            if field in schedule and phase_schedule.get(field) != str(schedule[field]):
                                phase_schedule[field] = str(schedule[field])
                                changed = True
                if changed:
                    client.set_phases({"phases": updated})
        if changed:
            phases[:] = deepcopy(updated)
            self._last_phase_snapshot = deepcopy(updated)
            log.info("Reapplied saved OpenLisaPro phase settings to Lisa Pro")
        return phases

    def _restore_temperature_led(self, phases: list[dict[str, Any]], cfg: dict[str, Any]) -> None:
        if self._temp_led_key is None:
            return
        phase_id, led_mode = self._temp_led_key
        base = self._temperature_led_base(cfg, phases, phase_id, led_mode)
        if base is not None:
            self._write_temperature_led(phases, phase_id, led_mode, base)
        self._clear_temperature_led_override()

    def reset_temperature_led_override(self, phase_id: Optional[int] = None) -> None:
        """Forget a temporary dimming level after a manual LED setting save."""
        if phase_id is not None and self._temp_led_key is not None:
            if self._temp_led_key[0] != int(phase_id):
                return
        self._clear_temperature_led_override()

    def _clear_temperature_led_override(self) -> None:
        self._temp_led_pct = None
        self._temp_led_key = None
        self._last_temp_led_at = None
        self.store.update({"temperature_led_override": None})

    def _reduce_temperature_led(
        self,
        phases: list[dict[str, Any]],
        cfg: dict[str, Any],
        phase_id: int,
        led_mode: str,
        *,
        now: float,
        halve: bool = False,
    ) -> None:
        base = self._temperature_led_base(cfg, phases, phase_id, led_mode)
        if base is None or base <= 0:
            return
        if self._temp_led_key != (int(phase_id), led_mode):
            self._temp_led_key = (int(phase_id), led_mode)
            self._temp_led_pct = base
        if self._temp_led_pct <= 0:
            return
        if self._last_temp_led_at is not None and now - self._last_temp_led_at < TEMPERATURE_RESPONSE_HOLD_S:
            return
        minimum = min(base, max(TEMPERATURE_LED_MIN_PCT, base * 0.25))
        current = min(self._temp_led_pct, base)
        desired = (
            current * TEMPERATURE_LED_REDUCTION_FACTOR
            if halve
            else current - TEMPERATURE_LED_STEP_PCT
        )
        reduced = max(minimum, desired)
        if reduced != self._temp_led_pct:
            self._write_temperature_led(phases, phase_id, led_mode, reduced)
            self._temp_led_pct = reduced
            self._last_temp_led_at = now
            self.store.update(
                {
                    "temperature_led_override": {
                        "phase_id": str(phase_id),
                        "mode": led_mode,
                        "base_pct": base,
                        "current_pct": reduced,
                        "last_at": now,
                    }
                }
            )

    @staticmethod
    def _temperature_warning(
        active: bool,
        outside_limited: bool,
        led_reduced: bool,
        limit: Optional[float],
        outside: Optional[float],
    ) -> Optional[str]:
        if not active:
            return None
        if outside_limited:
            if led_reduced:
                led_message = "LED power is temporarily reduced."
            else:
                led_message = "LED is already at 0%."
            return f"Outside air is {outside:.1f}°C, at or above the {limit:.1f}°C limit; cooling below the limit may not be possible. Fan is at maximum. {led_message}"
        if led_reduced:
            return "Fan reached its maximum and temperature stayed high; LED power is temporarily reduced."
        return "Temperature is above the stage limit; fan is at maximum speed."

    def _append_history(self, now: float, *, device_measurements: Optional[dict[str, Any]] = None) -> None:
        s = self._state
        point = {
            "t": now,
            "fan_actual": s.get("fan_actual"),
            "fan_command": s.get("fan_command"),
            "fan_min_limit": s.get("fan_min_limit"),
            "fan_max_limit": s.get("fan_max_limit"),
            "vpd_inside": s.get("vpd_inside"),
            "vpd_outside": s.get("vpd_outside"),
            "vpd_target": s.get("vpd_target"),
            "pid_error": s.get("pid_error"),
            "throttling": s.get("throttling"),
            "reason": s.get("reason"),
            "mode": s.get("mode"),
        }
        self._history.append(point)
        if self._telemetry_store is not None:
            try:
                self._telemetry_store.record_measurement(
                    sampled_at=now,
                    device=device_measurements or {},
                    controller=deepcopy(s),
                    chart=point,
                    settings=self.store.get(),
                )
            except Exception:  # noqa: BLE001 — a storage failure must not stop hardware control
                log.exception("Unable to persist telemetry measurement")
            return
        if self._history_path is None:
            return
        try:
            self._history_path.parent.mkdir(parents=True, exist_ok=True)
            with self._history_path.open("a", encoding="utf-8") as history_file:
                history_file.write(json.dumps(point, separators=(",", ":")) + "\n")
            if now - self._last_history_prune >= 3600:
                if self._history and float(self._history[0].get("t", now)) < now - HISTORY_RETENTION_S:
                    self._prune_history_file(now)
                else:
                    self._last_history_prune = now
        except OSError:
            log.exception("Unable to persist controller history")

    def _load_history(self) -> None:
        if self._telemetry_store is not None:
            try:
                self._history.extend(
                    self._telemetry_store.recent_chart_history(
                        cutoff=time.time() - HISTORY_RETENTION_S,
                        limit=HISTORY_MAX,
                    )
                )
            except Exception:  # noqa: BLE001 — retain controller availability if chart loading fails
                log.exception("Unable to load controller history from SQLite")
            return
        if self._history_path is None or not self._history_path.exists():
            return
        cutoff = time.time() - HISTORY_RETENTION_S
        try:
            retained = []
            with self._history_path.open("r", encoding="utf-8") as history_file:
                for line in history_file:
                    try:
                        point = json.loads(line)
                        if isinstance(point, dict) and float(point.get("t", 0)) >= cutoff:
                            retained.append(point)
                    except (TypeError, ValueError, json.JSONDecodeError):
                        continue
            self._history.extend(retained[-HISTORY_MAX:])
            self._prune_history_file(time.time(), points=list(self._history))
        except OSError:
            log.exception("Unable to load controller history")

    def _prune_history_file(self, now: float, *, points: Optional[list[dict[str, Any]]] = None) -> None:
        if self._history_path is None:
            return
        cutoff = now - HISTORY_RETENTION_S
        retained = [p for p in (points if points is not None else list(self._history)) if float(p.get("t", 0)) >= cutoff]
        temporary = self._history_path.with_suffix(self._history_path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as history_file:
            for point in retained:
                history_file.write(json.dumps(point, separators=(",", ":")) + "\n")
        temporary.replace(self._history_path)
        self._last_history_prune = now

    @staticmethod
    def _resolve_mode(
        status: dict[str, Any],
        phase: Optional[dict[str, Any]] = None,
        *,
        now: Optional[datetime] = None,
    ) -> str:
        silent = status.get("silent") or {}
        schedule = (phase or {}).get("schedule") or {}
        start = FanPidController._parse_clock_time(schedule.get("on"))
        end = FanPidController._parse_clock_time(schedule.get("off"))
        current = now
        if current is None:
            ntp_time = status.get("ntp_time")
            if isinstance(ntp_time, str):
                for fmt in ("%d.%m.%y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%H:%M:%S"):
                    try:
                        current = datetime.strptime(ntp_time, fmt)
                        break
                    except ValueError:
                        continue
            current = current or datetime.now()

        if start is not None and end is not None:
            clock = current.time()
            if start == end:
                is_day = True
            elif start < end:
                is_day = start <= clock < end
            else:
                # Light schedules often cross midnight (for example, 21:00–11:00).
                is_day = clock >= start or clock < end
        else:
            # Older devices may omit schedules; retain their reported light state.
            is_day = bool(status.get("light_on"))

        if silent.get("active"):
            return "day_silent" if is_day else "night_silent"
        if is_day:
            return "day"
        return "night"

    @staticmethod
    def _parse_clock_time(value: Any) -> Optional[datetime_time]:
        if not isinstance(value, str):
            return None
        try:
            return datetime.strptime(value, "%H:%M").time()
        except ValueError:
            return None

    @staticmethod
    def _resolve_phase(
        status: dict[str, Any], phases: list[dict[str, Any]]
    ) -> tuple[Optional[dict[str, Any]], int]:
        if not phases:
            return None, 0
        label = str(((status.get("grow") or {}).get("phase") or "")).lower()
        # Prefer name match
        for phase in phases:
            name = str(phase.get("name") or "").lower()
            if label and (name in label or label in name):
                return phase, int(phase.get("id", 0))
        for _, hints in PHASE_NAME_HINTS:
            if any(h in label for h in hints):
                for phase in phases:
                    name = str(phase.get("name") or "").lower()
                    if any(h in name for h in hints):
                        return phase, int(phase.get("id", 0))
        # Fallback: flowering often last stage
        if any(h in label for h in ("blüte", "bluete", "blute", "flower")):
            phase = phases[-1]
            return phase, int(phase.get("id", len(phases) - 1))
        # Default to middle / last known
        phase = phases[min(2, len(phases) - 1)] if len(phases) >= 3 else phases[-1]
        return phase, int(phase.get("id", 0))

    @staticmethod
    def _vpd_targets(
        cfg: dict[str, Any],
        phase_id: int,
        phase: Optional[dict[str, Any]],
        mode: str,
    ) -> tuple[float, float]:
        targets = (cfg.get("vpd_targets") or {}).get(str(phase_id)) or {}
        if mode in targets:
            lo = float(targets[mode]["vpd_min"])
            hi = float(targets[mode]["vpd_max"])
        else:
            settings = ((phase or {}).get("settings") or {}).get(mode) or {}
            lo = float(settings.get("vpd_min", 0.8))
            hi = float(settings.get("vpd_max", 1.2))
        if lo > hi:
            lo, hi = hi, lo
        return lo, hi

    @staticmethod
    def _fan_limits(
        cfg: dict[str, Any],
        phase_id: int,
        mode: str,
        phase: Optional[dict[str, Any]],
    ) -> dict[str, float]:
        limits = (cfg.get("fan_limits") or {}).get(str(phase_id)) or {}
        if mode in limits:
            return {
                "fan_min": float(limits[mode]["fan_min"]),
                "fan_max": float(limits[mode]["fan_max"]),
            }
        # fallback any mode / device settings
        for m in MODES:
            if m in limits:
                return {
                    "fan_min": float(limits[m]["fan_min"]),
                    "fan_max": float(limits[m]["fan_max"]),
                }
        settings = ((phase or {}).get("settings") or {}).get(mode) or {}
        return {
            "fan_min": float(settings.get("fan_min", 10)),
            "fan_max": float(settings.get("fan_max", 80)),
        }
