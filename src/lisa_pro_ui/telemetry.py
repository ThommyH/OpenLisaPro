"""Durable sensor, controller, settings-snapshot, and flow-event storage."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_number(*values: Any) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None:
            return number
    return None


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return None


class TelemetryStore:
    """SQLite storage for all new measurements and controller flow events.

    Measurement rows keep the full device response and controller state as JSON
    columns for forward compatibility, plus common climate/control values as
    SQL columns for efficient analysis. Settings are stored once per change and
    linked to each measurement/event by foreign key.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings_snapshots (
                    id INTEGER PRIMARY KEY,
                    captured_at REAL NOT NULL,
                    config_hash TEXT NOT NULL,
                    config_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY,
                    source_key TEXT UNIQUE,
                    sampled_at REAL NOT NULL,
                    settings_snapshot_id INTEGER REFERENCES settings_snapshots(id),
                    temp_inside_c REAL,
                    humidity_inside_rh REAL,
                    dew_point_inside_c REAL,
                    temp_outside_c REAL,
                    humidity_outside_rh REAL,
                    dew_point_outside_c REAL,
                    vpd_inside_kpa REAL,
                    vpd_outside_kpa REAL,
                    fan_actual_pct REAL,
                    fan_command_pct REAL,
                    phase_id TEXT,
                    mode TEXT,
                    device_json TEXT NOT NULL,
                    controller_json TEXT NOT NULL,
                    chart_json TEXT NOT NULL,
                    is_legacy INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS measurements_sampled_at_idx
                    ON measurements(sampled_at, id);
                CREATE INDEX IF NOT EXISTS measurements_settings_idx
                    ON measurements(settings_snapshot_id);

                CREATE TABLE IF NOT EXISTS flow_events (
                    id INTEGER PRIMARY KEY,
                    source_key TEXT UNIQUE,
                    occurred_at_ms INTEGER NOT NULL,
                    settings_snapshot_id INTEGER REFERENCES settings_snapshots(id),
                    event_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS flow_events_occurred_at_idx
                    ON flow_events(occurred_at_ms DESC, id DESC);

                CREATE TABLE IF NOT EXISTS replication_state (
                    primary_url TEXT PRIMARY KEY,
                    measurement_id INTEGER NOT NULL DEFAULT 0,
                    flow_event_id INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            measurement_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(measurements)")
            }
            if "is_legacy" not in measurement_columns:
                connection.execute(
                    "ALTER TABLE measurements ADD COLUMN is_legacy INTEGER NOT NULL DEFAULT 0"
                )

    def replication_cursor(self, primary_url: str) -> tuple[int, int]:
        with self._connection() as connection:
            row = connection.execute(
                "SELECT measurement_id, flow_event_id FROM replication_state WHERE primary_url = ?",
                (primary_url,),
            ).fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def export_replication_batch(
        self, *, after_measurement_id: int, after_flow_event_id: int, limit: int = 250
    ) -> dict[str, Any]:
        with self._connection() as connection:
            measurement_rows = connection.execute(
                self._measurement_select() + " WHERE m.id > ? ORDER BY m.id LIMIT ?",
                (after_measurement_id, limit),
            ).fetchall()
            event_rows = connection.execute(
                """SELECT e.id, e.event_json, s.config_json AS settings_json
                   FROM flow_events e LEFT JOIN settings_snapshots s ON s.id=e.settings_snapshot_id
                   WHERE e.id > ? ORDER BY e.id LIMIT ?""",
                (after_flow_event_id, limit),
            ).fetchall()
        measurements = [self._decode_measurement(row) for row in measurement_rows]
        events = []
        for row in event_rows:
            events.append({
                "id": int(row["id"]),
                "event": json.loads(row["event_json"]),
                "settings": json.loads(row["settings_json"]) if row["settings_json"] else None,
            })
        return {
            "measurements": measurements,
            "flow_events": events,
            "measurement_cursor": measurements[-1]["id"] if measurements else after_measurement_id,
            "flow_event_cursor": events[-1]["id"] if events else after_flow_event_id,
        }

    def import_replication_batch(
        self, primary_url: str, payload: dict[str, Any]
    ) -> None:
        measurements = payload.get("measurements", [])
        events = payload.get("flow_events", [])
        with self._lock, self._connection() as connection:
            for item in measurements:
                if not isinstance(item, dict) or not isinstance(item.get("id"), int):
                    continue
                source_key = f"{primary_url}:measurement:{item['id']}"
                if connection.execute("SELECT 1 FROM measurements WHERE source_key=?", (source_key,)).fetchone():
                    continue
                metrics = item.get("metrics") or {}
                device = item.get("device") or {}
                controller = item.get("controller") or {}
                chart = item.get("chart") or {}
                settings = item.get("settings")
                snapshot_id = self._snapshot_id(connection, float(item.get("t", 0)), settings)
                connection.execute(
                    """INSERT INTO measurements(
                       source_key, sampled_at, settings_snapshot_id, temp_inside_c, humidity_inside_rh,
                       dew_point_inside_c, temp_outside_c, humidity_outside_rh, dew_point_outside_c,
                       vpd_inside_kpa, vpd_outside_kpa, fan_actual_pct, fan_command_pct, phase_id, mode,
                       device_json, controller_json, chart_json, is_legacy)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (source_key, float(item.get("t", 0)), snapshot_id,
                     _first_number(metrics.get("temp_inside_c"), device.get("temp_c"), controller.get("temperature_inside_c")),
                     _first_number(metrics.get("humidity_inside_rh"), device.get("humi_rh"), controller.get("humidity_inside_rh")),
                     _first_number(metrics.get("dew_point_inside_c"), device.get("dew_c"), controller.get("dew_point_inside_c")),
                     _first_number(metrics.get("temp_outside_c"), device.get("temp_out_c"), controller.get("temperature_outside_c")),
                     _first_number(metrics.get("humidity_outside_rh"), device.get("humi_out_rh"), controller.get("humidity_outside_rh")),
                     _first_number(metrics.get("dew_point_outside_c"), device.get("dew_out_c"), controller.get("dew_point_outside_c")),
                     _first_number(metrics.get("vpd_inside_kpa"), controller.get("vpd_inside"), chart.get("vpd_inside")),
                     _first_number(metrics.get("vpd_outside_kpa"), controller.get("vpd_outside"), chart.get("vpd_outside")),
                     _first_number(metrics.get("fan_actual_pct"), controller.get("fan_actual"), device.get("fan_pct"), chart.get("fan_actual")),
                     _first_number(metrics.get("fan_command_pct"), controller.get("fan_command"), chart.get("fan_command")),
                     _first_text(metrics.get("phase_id"), controller.get("phase_id"), chart.get("phase_id")),
                     _first_text(metrics.get("mode"), controller.get("mode"), chart.get("mode")),
                     _json(device), _json(controller), _json(chart),
                     int(bool(item.get("legacy"))))
                )
            for item in events:
                if not isinstance(item, dict) or not isinstance(item.get("id"), int) or not isinstance(item.get("event"), dict):
                    continue
                event_hash = hashlib.sha256(_json(item["event"]).encode("utf-8")).hexdigest()
                source_key = f"{primary_url}:flow-event:{event_hash}"
                if connection.execute("SELECT 1 FROM flow_events WHERE source_key=?", (source_key,)).fetchone():
                    continue
                event = item["event"]
                snapshot_id = self._snapshot_id(connection, float(event.get("at", 0)) / 1000, item.get("settings"))
                connection.execute(
                    "INSERT INTO flow_events(source_key, occurred_at_ms, settings_snapshot_id, event_json) VALUES(?,?,?,?)",
                    (source_key, int(event.get("at", 0)), snapshot_id, _json(event)),
                )
            connection.execute(
                """INSERT INTO replication_state(primary_url, measurement_id, flow_event_id) VALUES(?,?,?)
                   ON CONFLICT(primary_url) DO UPDATE SET measurement_id=MAX(measurement_id, excluded.measurement_id),
                   flow_event_id=MAX(flow_event_id, excluded.flow_event_id)""",
                (primary_url, int(payload.get("measurement_cursor", 0)), int(payload.get("flow_event_cursor", 0))),
            )

    @staticmethod
    def _snapshot_id(
        connection: sqlite3.Connection,
        captured_at: float,
        settings: dict[str, Any] | None,
    ) -> int | None:
        if settings is None:
            return None
        config_json = _json(settings)
        config_hash = hashlib.sha256(config_json.encode("utf-8")).hexdigest()
        current = connection.execute(
            "SELECT id, config_hash FROM settings_snapshots ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if current and current["config_hash"] == config_hash:
            return int(current["id"])
        cursor = connection.execute(
            "INSERT INTO settings_snapshots(captured_at, config_hash, config_json) VALUES (?, ?, ?)",
            (captured_at, config_hash, config_json),
        )
        return int(cursor.lastrowid)

    @classmethod
    def _insert_measurement(
        cls,
        connection: sqlite3.Connection,
        *,
        sampled_at: float,
        device: dict[str, Any],
        controller: dict[str, Any],
        chart: dict[str, Any],
        settings: dict[str, Any] | None,
    ) -> None:
        snapshot_id = cls._snapshot_id(connection, sampled_at, settings)
        connection.execute(
            """
            INSERT INTO measurements(
                sampled_at, settings_snapshot_id,
                temp_inside_c, humidity_inside_rh, dew_point_inside_c,
                temp_outside_c, humidity_outside_rh, dew_point_outside_c,
                vpd_inside_kpa, vpd_outside_kpa, fan_actual_pct, fan_command_pct,
                phase_id, mode, device_json, controller_json, chart_json, is_legacy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                sampled_at,
                snapshot_id,
                _first_number(device.get("temp_c"), controller.get("temperature_inside_c")),
                _first_number(device.get("humi_rh"), controller.get("humidity_inside_rh")),
                _first_number(device.get("dew_c"), controller.get("dew_point_inside_c")),
                _first_number(device.get("temp_out_c"), controller.get("temperature_outside_c")),
                _first_number(device.get("humi_out_rh"), controller.get("humidity_outside_rh")),
                _first_number(device.get("dew_out_c"), controller.get("dew_point_outside_c")),
                _first_number(controller.get("vpd_inside"), chart.get("vpd_inside")),
                _first_number(controller.get("vpd_outside"), chart.get("vpd_outside")),
                _first_number(controller.get("fan_actual"), device.get("fan_pct"), chart.get("fan_actual")),
                _first_number(controller.get("fan_command"), chart.get("fan_command")),
                _first_text(controller.get("phase_id"), chart.get("phase_id")),
                _first_text(controller.get("mode"), chart.get("mode")),
                _json(device),
                _json(controller),
                _json(chart),
            ),
        )

    def record_measurement(
        self,
        *,
        sampled_at: float,
        device: dict[str, Any],
        controller: dict[str, Any],
        chart: dict[str, Any],
        settings: dict[str, Any],
    ) -> None:
        with self._lock, self._connection() as connection:
            self._insert_measurement(
                connection,
                sampled_at=sampled_at,
                device=device,
                controller=controller,
                chart=chart,
                settings=settings,
            )

    def record_flow_event(self, event: dict[str, Any], settings: dict[str, Any]) -> int:
        occurred_at_ms = int(event["at"])
        with self._lock, self._connection() as connection:
            snapshot_id = self._snapshot_id(connection, occurred_at_ms / 1000.0, settings)
            cursor = connection.execute(
                "INSERT INTO flow_events(occurred_at_ms, settings_snapshot_id, event_json) VALUES (?, ?, ?)",
                (occurred_at_ms, snapshot_id, _json(event)),
            )
            return int(cursor.lastrowid)

    def flow_event_count(self) -> int:
        with self._connection() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM flow_events").fetchone()[0])

    def list_flow_events(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT e.event_json, s.config_json AS settings_json
                FROM flow_events e
                LEFT JOIN settings_snapshots s ON s.id = e.settings_snapshot_id
                ORDER BY e.occurred_at_ms DESC, e.id DESC LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        events = []
        for row in rows:
            event = json.loads(row["event_json"])
            event["settings"] = json.loads(row["settings_json"]) if row["settings_json"] is not None else None
            events.append(event)
        return events

    def recent_chart_history(self, *, cutoff: float, limit: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                """
                SELECT chart_json FROM (
                    SELECT chart_json, sampled_at, id
                    FROM measurements
                    WHERE sampled_at >= ?
                    ORDER BY sampled_at DESC, id DESC
                    LIMIT ?
                ) ORDER BY sampled_at ASC, id ASC
                """,
                (cutoff, limit),
            ).fetchall()
        return [json.loads(row["chart_json"]) for row in rows]

    @staticmethod
    def _decode_measurement(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "t": float(row["sampled_at"]),
            "metrics": {
                "temp_inside_c": row["temp_inside_c"],
                "humidity_inside_rh": row["humidity_inside_rh"],
                "dew_point_inside_c": row["dew_point_inside_c"],
                "temp_outside_c": row["temp_outside_c"],
                "humidity_outside_rh": row["humidity_outside_rh"],
                "dew_point_outside_c": row["dew_point_outside_c"],
                "vpd_inside_kpa": row["vpd_inside_kpa"],
                "vpd_outside_kpa": row["vpd_outside_kpa"],
                "fan_actual_pct": row["fan_actual_pct"],
                "fan_command_pct": row["fan_command_pct"],
                "phase_id": row["phase_id"],
                "mode": row["mode"],
            },
            "device": json.loads(row["device_json"]),
            "controller": json.loads(row["controller_json"]),
            "chart": json.loads(row["chart_json"]),
            "settings": json.loads(row["settings_json"]) if row["settings_json"] is not None else None,
            "legacy": bool(row["is_legacy"]) if "is_legacy" in row.keys() else False,
        }

    @staticmethod
    def _measurement_select() -> str:
        return """
            SELECT m.*, s.config_json AS settings_json
            FROM measurements m
            LEFT JOIN settings_snapshots s ON s.id = m.settings_snapshot_id
        """

    def measurement_count(self, *, start: float | None = None, end: float | None = None) -> int:
        clauses: list[str] = []
        params: list[float] = []
        if start is not None:
            clauses.append("sampled_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("sampled_at <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as connection:
            row = connection.execute(f"SELECT COUNT(*) FROM measurements{where}", params).fetchone()
        return int(row[0])

    def list_measurements(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                self._measurement_select() + " ORDER BY m.sampled_at DESC, m.id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._decode_measurement(row) for row in rows]

    def iter_measurements(
        self, *, start: float | None = None, end: float | None = None
    ) -> Iterator[dict[str, Any]]:
        clauses: list[str] = []
        params: list[float] = []
        if start is not None:
            clauses.append("m.sampled_at >= ?")
            params.append(start)
        if end is not None:
            clauses.append("m.sampled_at <= ?")
            params.append(end)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        connection = self._connect()
        try:
            rows = connection.execute(
                self._measurement_select() + where + " ORDER BY m.sampled_at, m.id", params
            )
            for row in rows:
                yield self._decode_measurement(row)
        finally:
            connection.close()
