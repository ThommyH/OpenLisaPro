"""Persistent local control overrides (VPD, fan limits, LED, PID)."""

from __future__ import annotations

import json
import os
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

MODES = ("day", "day_silent", "night", "night_silent")
LED_MODES = ("day", "day_silent", "night", "night_silent")

DEFAULT_PID = {
    "kp": 70.0,
    "ki": 0.12,
    "kd": 6.0,
    # Integral accumulates VPD error in kPa·s. At the default Ki, 40 only
    # allows a 4.8 percentage-point correction, too little to clear sustained
    # VPD errors. 200 allows up to 24 points while still bounding windup.
    "integral_limit": 200.0,
    "ramp_pct_per_min": 15.0,
    "interval_s": 5.0,
    "deadband_kpa": 0.03,
    "temperature_weight_pct_per_c": 2.0,
    "unreachable_error_kpa": 0.08,
    "unreachable_hold_s": 90.0,
    "throttle_step_pct": 3.0,
    "throttle_interval_s": 30.0,
}

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "vpd_targets": {},  # phase_id -> mode -> {vpd_min, vpd_max}
    "fan_limits": {},  # phase_id -> mode -> {fan_min, fan_max}
    "temperature_limits": {},  # phase_id -> hard {max_temp_c}
    "temperature_soft_limits": {},  # phase_id -> optional {min_temp_c, max_temp_c}
    "temperature_led_override": None,  # temporary controller LED reduction, restored after cooling
    "led": {},  # phase_id -> mode -> percent
    "pid": dict(DEFAULT_PID),
}


def default_config() -> dict[str, Any]:
    """Return defaults, allowing deployments to opt out of enabled PID."""
    config = deepcopy(DEFAULT_CONFIG)
    value = os.environ.get("LISA_PRO_PID_ENABLED")
    if value is not None:
        config["enabled"] = value.strip().lower() not in {"0", "false", "no", "off", "disabled"}
    return config


class ConfigStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data = default_config()
        self._legacy_vpd: dict[str, float] | None = None
        self.load()

    def load(self) -> dict[str, Any]:
        with self._lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._data = self._merge(default_config(), raw if isinstance(raw, dict) else {})
                except (OSError, json.JSONDecodeError):
                    self._data = default_config()
                    self._legacy_vpd = None
            else:
                self._data = default_config()
                self._legacy_vpd = None
            return deepcopy(self._data)

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)

    def get(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def reset(self) -> dict[str, Any]:
        """Restore local control settings to built-in defaults and persist them."""
        with self._lock:
            self._data = default_config()
            self._legacy_vpd = None
            self.save()
            return deepcopy(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if "enabled" in patch:
                self._data["enabled"] = bool(patch["enabled"])
            if "vpd_targets" in patch and isinstance(patch["vpd_targets"], dict):
                self._data["vpd_targets"] = self._normalize_vpd_targets(patch["vpd_targets"])
            if "fan_limits" in patch and isinstance(patch["fan_limits"], dict):
                self._data["fan_limits"] = self._normalize_fan_limits(patch["fan_limits"])
            if "temperature_limits" in patch and isinstance(patch["temperature_limits"], dict):
                self._data["temperature_limits"] = self._normalize_temperature_limits(patch["temperature_limits"])
            if "temperature_soft_limits" in patch and isinstance(patch["temperature_soft_limits"], dict):
                self._data["temperature_soft_limits"] = self._normalize_temperature_soft_limits(
                    patch["temperature_soft_limits"]
                )
            if "temperature_led_override" in patch:
                self._data["temperature_led_override"] = self._normalize_temperature_led_override(
                    patch["temperature_led_override"]
                )
            if "led" in patch and isinstance(patch["led"], dict):
                self._data["led"] = self._normalize_led(patch["led"])
            if "pid" in patch and isinstance(patch["pid"], dict):
                self._data["pid"] = self._coerce_global_pid(patch["pid"])
            self.save()
            return deepcopy(self._data)

    def ensure_fan_limits_from_phases(self, phases: list[dict[str, Any]]) -> dict[str, Any]:
        """Seed missing fan / LED / VPD overrides from device phase settings."""
        with self._lock:
            changed = False
            limits = self._data.setdefault("fan_limits", {})
            led = self._data.setdefault("led", {})
            vpd = self._data.setdefault("vpd_targets", {})
            legacy = self._legacy_vpd
            if legacy:
                self._legacy_vpd = None
                changed = True

            for phase in phases or []:
                phase_id = str(phase.get("id", 0))
                settings = phase.get("settings") or {}
                bucket = limits.setdefault(phase_id, {})
                led_bucket = led.setdefault(phase_id, {})
                vpd_bucket = vpd.setdefault(phase_id, {})
                for mode in MODES:
                    source_mode = "day" if mode == "day_silent" else mode
                    m = settings.get(mode) or settings.get(source_mode) or {}
                    if mode not in bucket:
                        source_limits = bucket.get(source_mode) or {}
                        bucket[mode] = {
                            "fan_min": float(source_limits.get("fan_min", m.get("fan_min", 20))),
                            "fan_max": float(source_limits.get("fan_max", m.get("fan_max", 80))),
                        }
                        changed = True
                    if mode not in vpd_bucket:
                        if legacy:
                            vpd_bucket[mode] = {
                                "vpd_min": float(legacy["vpd_min"]),
                                "vpd_max": float(legacy["vpd_max"]),
                            }
                        else:
                            source_targets = vpd_bucket.get(source_mode) or {}
                            lo = float(source_targets.get("vpd_min", m.get("vpd_min", 0.8)))
                            hi = float(source_targets.get("vpd_max", m.get("vpd_max", 1.2)))
                            if lo > hi:
                                lo, hi = hi, lo
                            vpd_bucket[mode] = {"vpd_min": lo, "vpd_max": hi}
                        changed = True
                    if mode in LED_MODES and mode not in led_bucket:
                        if mode in ("night", "night_silent"):
                            led_bucket[mode] = 0.0
                        else:
                            led_value = m.get("led")
                            if led_value is None and mode == "day_silent":
                                led_value = (((phase.get("settings") or {}).get("day") or {}).get("led", 0))
                            led_bucket[mode] = float(max(0.0, min(100.0, float(led_value or 0))))
                        changed = True
            if changed:
                self.save()
            return deepcopy(self._data)

    def ensure_stage_settings_from_phases(self, phases: list[dict[str, Any]]) -> dict[str, Any]:
        return self.ensure_fan_limits_from_phases(phases)

    def reset_stage_from_phase(self, phase_id: int | str, phase: dict[str, Any]) -> dict[str, Any]:
        """Reset one stage's local overrides from its factory device phase."""
        key = str(phase_id)
        with self._lock:
            for name in ("fan_limits", "vpd_targets", "led", "temperature_limits", "temperature_soft_limits"):
                values = self._data.setdefault(name, {})
                values.pop(key, None)
            override = self._data.get("temperature_led_override")
            if isinstance(override, dict) and str(override.get("phase_id")) == key:
                self._data["temperature_led_override"] = None
            return self.ensure_fan_limits_from_phases([phase])

    @classmethod
    def _normalize_pid_values(cls, raw: dict[str, Any]) -> dict[str, Any]:
        out = dict(DEFAULT_PID)
        for key, default in DEFAULT_PID.items():
            if key in raw and raw[key] is not None:
                out[key] = type(default)(raw[key])
        out["temperature_weight_pct_per_c"] = max(
            0.0, min(20.0, out["temperature_weight_pct_per_c"])
        )
        # Migrate former built-in PID defaults. The old values were also the
        # shipped settings and produced a weak response to large VPD errors.
        if out["kp"] == 35.0:
            out["kp"] = DEFAULT_PID["kp"]
        if out["integral_limit"] == 40.0:
            out["integral_limit"] = DEFAULT_PID["integral_limit"]
        return out

    @classmethod
    def _coerce_global_pid(cls, raw: dict[str, Any]) -> dict[str, Any]:
        if cls._is_flat_pid(raw):
            return cls._normalize_pid_values(raw)
        for value in raw.values():
            if isinstance(value, dict) and cls._is_flat_pid(value):
                return cls._normalize_pid_values(value)
        return dict(DEFAULT_PID)

    @staticmethod
    def _is_flat_pid(raw: dict[str, Any]) -> bool:
        return any(
            k in raw
            for k in ("kp", "ki", "kd", "ramp_pct_per_min", "interval_s", "temperature_weight_pct_per_c")
        )

    @staticmethod
    def _normalize_fan_limits(raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for phase_id, modes in raw.items():
            if not isinstance(modes, dict):
                continue
            bucket: dict[str, Any] = {}
            for mode in MODES:
                m = modes.get(mode)
                if not isinstance(m, dict):
                    continue
                fan_min = float(m.get("fan_min", 0))
                fan_max = float(m.get("fan_max", 100))
                fan_min = max(0.0, min(100.0, fan_min))
                fan_max = max(0.0, min(100.0, fan_max))
                if fan_min > fan_max:
                    fan_min, fan_max = fan_max, fan_min
                bucket[mode] = {"fan_min": fan_min, "fan_max": fan_max}
            if bucket:
                out[str(phase_id)] = bucket
        return out

    @staticmethod
    def _normalize_temperature_limits(raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for phase_id, values in raw.items():
            if not isinstance(values, dict) or values.get("max_temp_c") in (None, ""):
                continue
            max_temp = float(values["max_temp_c"])
            out[str(phase_id)] = {"max_temp_c": max(5.0, min(50.0, max_temp))}
        return out

    @staticmethod
    def _normalize_temperature_soft_limits(raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for phase_id, values in raw.items():
            if not isinstance(values, dict):
                continue
            bucket: dict[str, float] = {}
            for field in ("min_temp_c", "max_temp_c"):
                value = values.get(field)
                if value in (None, ""):
                    continue
                bucket[field] = max(5.0, min(50.0, float(value)))
            if "min_temp_c" in bucket and "max_temp_c" in bucket and bucket["min_temp_c"] > bucket["max_temp_c"]:
                bucket["min_temp_c"], bucket["max_temp_c"] = bucket["max_temp_c"], bucket["min_temp_c"]
            if bucket:
                out[str(phase_id)] = bucket
        return out

    @staticmethod
    def _normalize_temperature_led_override(raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or raw.get("mode") not in LED_MODES:
            return None
        try:
            base = max(0.0, min(100.0, float(raw["base_pct"])))
            current = max(0.0, min(100.0, float(raw["current_pct"])))
            last_at = float(raw["last_at"])
        except (KeyError, TypeError, ValueError):
            return None
        return {
            "phase_id": str(raw.get("phase_id", "")),
            "mode": raw["mode"],
            "base_pct": base,
            "current_pct": current,
            "last_at": last_at,
        }

    @staticmethod
    def _normalize_vpd_targets(raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for phase_id, modes in raw.items():
            if not isinstance(modes, dict):
                continue
            bucket: dict[str, Any] = {}
            for mode in MODES:
                m = modes.get(mode)
                if not isinstance(m, dict):
                    continue
                lo = float(m.get("vpd_min", 0.8))
                hi = float(m.get("vpd_max", 1.2))
                lo = max(0.0, min(2.5, lo))
                hi = max(0.0, min(2.5, hi))
                if lo > hi:
                    lo, hi = hi, lo
                bucket[mode] = {"vpd_min": lo, "vpd_max": hi}
            if bucket:
                out[str(phase_id)] = bucket
        return out

    @staticmethod
    def _normalize_led(raw: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for phase_id, modes in raw.items():
            if not isinstance(modes, dict):
                continue
            bucket: dict[str, Any] = {}
            for mode in LED_MODES:
                if mode in ("night", "night_silent"):
                    bucket[mode] = 0.0
                    continue
                if mode not in modes or modes[mode] is None:
                    continue
                bucket[mode] = float(max(0.0, min(100.0, float(modes[mode]))))
            if bucket:
                out[str(phase_id)] = bucket
        return out

    def _merge(self, base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
        out = deepcopy(base)
        legacy_overwrite = patch.get("vpd_overwrite") if isinstance(patch.get("vpd_overwrite"), dict) else None

        for key, value in patch.items():
            if key == "vpd_overwrite":
                continue
            if key == "pid" and isinstance(value, dict):
                out["pid"] = deepcopy(value)
            elif key in out and isinstance(out[key], dict) and isinstance(value, dict):
                nested = deepcopy(out[key])
                nested.update(value)
                out[key] = nested
            else:
                out[key] = deepcopy(value)

        out.setdefault("vpd_targets", {})
        out.setdefault("fan_limits", {})
        out.setdefault("temperature_limits", {})
        out.setdefault("temperature_soft_limits", {})
        out.setdefault("temperature_led_override", None)
        out.setdefault("led", {})
        out.setdefault("enabled", True)
        if not isinstance(out.get("temperature_limits"), dict):
            out["temperature_limits"] = {}
        if not isinstance(out.get("temperature_soft_limits"), dict):
            out["temperature_soft_limits"] = {}
        out["pid"] = self._coerce_global_pid(out.get("pid") if isinstance(out.get("pid"), dict) else {})
        if isinstance(out.get("led"), dict):
            out["led"] = self._normalize_led(out["led"])
        if isinstance(out.get("vpd_targets"), dict):
            out["vpd_targets"] = self._normalize_vpd_targets(out["vpd_targets"])
        if isinstance(out.get("temperature_limits"), dict):
            out["temperature_limits"] = self._normalize_temperature_limits(out["temperature_limits"])
        if isinstance(out.get("temperature_soft_limits"), dict):
            out["temperature_soft_limits"] = self._normalize_temperature_soft_limits(
                out["temperature_soft_limits"]
            )
        out["temperature_led_override"] = self._normalize_temperature_led_override(
            out.get("temperature_led_override")
        )

        # Migrate legacy global overwrite → seed template for empty stages
        if legacy_overwrite and legacy_overwrite.get("enabled") and not out["vpd_targets"]:
            lo = float(legacy_overwrite.get("vpd_min", 1.0))
            hi = float(legacy_overwrite.get("vpd_max", 1.2))
            if lo > hi:
                lo, hi = hi, lo
            self._legacy_vpd = {"vpd_min": lo, "vpd_max": hi}

        return out
