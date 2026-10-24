"""Durable sensor, controller, settings-snapshot, and flow-event storage."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)


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
                    occurred_at_ms INTEGER NOT NULL,
                    settings_snapshot_id INTEGER REFERENCES settings_snapshots(id),
                    event_json TEXT NOT NULL,
                    is_legacy INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS flow_events_occurred_at_idx
                    ON flow_events(occurred_at_ms DESC, id DESC);

                CREATE TABLE IF NOT EXISTS migrations (
                    name TEXT PRIMARY KEY,
                    completed_at REAL NOT NULL
                );
                """
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
        legacy: bool = False,
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
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sampled_at,
                snapshot_id,
                _number(device.get("temp_c", controller.get("temperature_inside_c"))),
                _number(device.get("humi_rh")),
                _number(device.get("dew_c")),
                _number(device.get("temp_out_c", controller.get("temperature_outside_c"))),
                _number(device.get("humi_out_rh")),
                _number(device.get("dew_out_c")),
                _number(controller.get("vpd_inside")),
                _number(controller.get("vpd_outside")),
                _number(controller.get("fan_actual", device.get("fan_pct"))),
                _number(controller.get("fan_command")),
                None if controller.get("phase_id") is None else str(controller.get("phase_id")),
                None if controller.get("mode") is None else str(controller.get("mode")),
                _json(device),
                _json(controller),
                _json(chart),
                int(legacy),
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
            "legacy": bool(row["is_legacy"]),
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

    def migrate_legacy_files(self, *, history_path: Path, flow_events_path: Path) -> None:
        """Import existing JSON histories once, without deleting the source files."""
        if history_path.is_file():
            try:
                with self._lock, self._connection() as connection:
                    if not connection.execute(
                        "SELECT 1 FROM migrations WHERE name = ?", ("controller-history-jsonl",)
                    ).fetchone():
                        with history_path.open("r", encoding="utf-8") as source:
                            for line in source:
                                try:
                                    point = json.loads(line)
                                    sampled_at = _number(point.get("t")) if isinstance(point, dict) else None
                                    if sampled_at is None:
                                        continue
                                    self._insert_measurement(
                                        connection,
                                        sampled_at=sampled_at,
                                        device={},
                                        controller=point,
                                        chart=point,
                                        settings=None,
                                        legacy=True,
                                    )
                                except (TypeError, ValueError, json.JSONDecodeError):
                                    continue
                        connection.execute(
                            "INSERT INTO migrations(name, completed_at) VALUES (?, strftime('%s','now'))",
                            ("controller-history-jsonl",),
                        )
            except OSError:
                log.exception("Could not migrate legacy controller history")

        if flow_events_path.is_file():
            try:
                payload = json.loads(flow_events_path.read_text(encoding="utf-8"))
                events = payload.get("events", []) if isinstance(payload, dict) else []
                if not isinstance(events, list):
                    events = []
                with self._lock, self._connection() as connection:
                    if not connection.execute(
                        "SELECT 1 FROM migrations WHERE name = ?", ("flow-events-json",)
                    ).fetchone():
                        for event in events:
                            if not isinstance(event, dict):
                                continue
                            at = _number(event.get("at"))
                            if at is None:
                                continue
                            connection.execute(
                                "INSERT INTO flow_events(occurred_at_ms, event_json, is_legacy) VALUES (?, ?, 1)",
                                (int(at), _json(event)),
                            )
                        connection.execute(
                            "INSERT INTO migrations(name, completed_at) VALUES (?, strftime('%s','now'))",
                            ("flow-events-json",),
                        )
            except (OSError, json.JSONDecodeError, sqlite3.Error):
                log.exception("Could not migrate legacy flow events")
