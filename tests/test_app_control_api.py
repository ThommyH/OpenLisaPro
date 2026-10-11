import json
import time
from pathlib import Path

from lisa_pro_ui.app import create_app


def test_version_endpoint_compares_current_build_with_latest_main_commit(tmp_path, monkeypatch):
    commit = "a" * 40

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return json.dumps({"sha": commit}).encode()

    monkeypatch.setenv("APP_GIT_COMMIT", commit)
    monkeypatch.setattr("lisa_pro_ui.app.urlopen", lambda request, timeout: FakeResponse())
    app = create_app(data_dir=tmp_path, start_controller=False)

    response = app.test_client().get("/api/version?refresh=1")

    assert response.status_code == 200
    body = response.get_json()
    assert body["current_commit"] == commit
    assert body["latest_commit"] == commit
    assert body["is_latest"] is True


def test_version_endpoint_matches_abbreviated_build_commit(tmp_path, monkeypatch):
    full_commit = "e64f0975504b2e2c489feebfeebbf76fec4ec41e"
    monkeypatch.setenv("APP_GIT_COMMIT", full_commit[:7])

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"sha": full_commit}).encode()

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", lambda *_args, **_kwargs: FakeResponse())
    app = create_app(data_dir=tmp_path, start_controller=False)

    body = app.test_client().get("/api/version?refresh=1").get_json()

    assert body["current_commit"] == full_commit[:7]
    assert body["is_latest"] is True


def test_control_get_and_post(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()

    got = client.get("/api/control")
    assert got.status_code == 200
    body = got.get_json()
    assert "config" in body
    assert "state" in body
    assert body["config"]["enabled"] is True
    assert "kp" in body["config"]["pid"]

    saved = client.post(
        "/api/control",
        json={
            "enabled": False,
            "vpd_targets": {
                "0": {
                    "day": {"vpd_min": 0.95, "vpd_max": 1.15},
                    "night": {"vpd_min": 0.9, "vpd_max": 1.1},
                    "night_silent": {"vpd_min": 0.9, "vpd_max": 1.1},
                }
            },
            "pid": {"kp": 42, "ki": 0.2, "kd": 5, "ramp_pct_per_min": 3, "interval_s": 4, "deadband_kpa": 0.02},
            "fan_limits": {
                "0": {
                    "day": {"fan_min": 20, "fan_max": 60},
                    "night": {"fan_min": 15, "fan_max": 40},
                    "night_silent": {"fan_min": 10, "fan_max": 30},
                }
            },
            "led": {"0": {"day": 45, "night": 0}},
            "temperature_limits": {"0": {"max_temp_c": 29.5}},
            "temperature_soft_limits": {"0": {"min_temp_c": 18, "max_temp_c": 26}},
        },
    )
    assert saved.status_code == 200
    cfg = saved.get_json()["config"]
    assert cfg["vpd_targets"]["0"]["day"]["vpd_min"] == 0.95
    assert cfg["pid"]["kp"] == 42.0
    assert cfg["fan_limits"]["0"]["day"]["fan_max"] == 60.0
    assert cfg["led"]["0"]["day"] == 45.0
    assert cfg["temperature_limits"]["0"]["max_temp_c"] == 29.5
    assert cfg["temperature_soft_limits"]["0"] == {"min_temp_c": 18.0, "max_temp_c": 26.0, "enabled": True}

    # Persisted on disk
    assert (tmp_path / "control.json").exists()
    again = client.get("/api/control").get_json()["config"]
    assert again["pid"]["kp"] == 42.0
    assert again["led"]["0"]["night"] == 0.0
    assert again["vpd_targets"]["0"]["night"]["vpd_max"] == 1.1
    assert again["temperature_limits"]["0"]["max_temp_c"] == 29.5
    assert again["temperature_soft_limits"]["0"]["max_temp_c"] == 26.0


def test_control_history_route_returns_retained_samples(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_controller=False)
    controller = app.config["FAN_CONTROLLER"]
    controller._history.append({"t": 1_800_000_000, "fan_command": 37, "vpd_inside": 1.1})

    response = app.test_client().get("/api/control/history")

    assert response.status_code == 200
    assert response.get_json()["history"][0]["fan_command"] == 37


def test_flow_events_persist_for_14_days_and_paginate_20_at_a_time(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()
    now_ms = int(time.time() * 1000)
    for index in range(22):
        response = client.post("/api/control/flow-events", json={
            "at": now_ms - index * 1000,
            "title": f"Flow change {index}",
            "steps": ["measure-vpd", "write-command"],
            "branches": ["soft-temperature-adjust"],
            "signature": f"change-{index}",
            "fan_change": {"from_pct": 20, "to_pct": 32, "delta_pct": 12, "temperature_boost_pct": 15, "command_pct": 24},
        })
        assert response.status_code == 201

    first = client.get("/api/control/flow-events?page=1").get_json()
    second = client.get("/api/control/flow-events?page=2").get_json()
    assert first["total"] == 22
    assert first["total_pages"] == 2
    assert first["page_size"] == len(first["events"]) == 20
    assert first["events"][0]["title"] == "Flow change 0"
    assert first["events"][0]["fan_change"] == {
        "from_pct": 20.0, "to_pct": 32.0, "delta_pct": 12.0,
        "temperature_boost_pct": 15.0, "command_pct": 24.0,
    }
    assert second["page"] == 2
    assert [event["title"] for event in second["events"]] == ["Flow change 20", "Flow change 21"]

    restarted = create_app(data_dir=tmp_path, start_controller=False).test_client()
    assert restarted.get("/api/control/flow-events?page=2").get_json()["total"] == 22


def test_flow_event_history_discards_entries_older_than_14_days(tmp_path: Path):
    now_ms = int(time.time() * 1000)
    history = {
        "schema_version": 1,
        "events": [
            {"at": now_ms - 15 * 24 * 60 * 60 * 1000, "title": "Expired", "steps": [], "branches": [], "signature": "expired"},
            {"at": now_ms - 1_000, "title": "Recent", "steps": [], "branches": [], "signature": "recent"},
        ],
    }
    (tmp_path / "flow-events.json").write_text(json.dumps(history), encoding="utf-8")
    client = create_app(data_dir=tmp_path, start_controller=False).test_client()

    response = client.get("/api/control/flow-events")

    assert response.get_json()["total"] == 1
    assert response.get_json()["events"][0]["title"] == "Recent"
    stored = json.loads((tmp_path / "flow-events.json").read_text(encoding="utf-8"))
    assert [event["title"] for event in stored["events"]] == ["Recent"]


def test_flow_event_page_exposes_history_pagination_controls(tmp_path: Path):
    client = create_app(data_dir=tmp_path, start_controller=False).test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Changes are saved for 14 days" in response.data
    assert b'id="flowEventPagination"' in response.data
    assert b'id="flowEventsPrevious"' in response.data
    assert b'id="flowEventsNext"' in response.data


def test_soft_temperature_limits_can_be_disabled_per_stage(tmp_path: Path):
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()
    response = client.post("/api/control", json={"temperature_soft_limits": {"0": {"enabled": False, "max_temp_c": 24}}})

    assert response.status_code == 200
    limits = response.get_json()["config"]["temperature_soft_limits"]["0"]
    assert limits == {"enabled": False, "max_temp_c": 24.0}


def test_saving_day_led_forces_night_and_night_silent_to_zero(tmp_path, monkeypatch):
    from lisa_pro_ui import app as app_module

    class FakeClient:
        phases = [
            {
                "id": 0,
                "settings": {
                    "day": {"led": 20},
                    "day_silent": {"led": 30},
                    "night": {"led": 50},
                    "night_silent": {"led": 60},
                },
            }
        ]

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_phases(self):
            return {"phases": self.phases}

        def set_phases(self, payload):
            type(self).phases = payload["phases"]

    monkeypatch.setattr(app_module, "LisaProClient", FakeClient)
    app = create_app(data_dir=tmp_path, start_controller=False)
    response = app.test_client().post(
        "/api/control", json={"led": {"0": {"day": 42, "day_silent": 65}}}
    )

    assert response.status_code == 200
    settings = FakeClient.phases[0]["settings"]
    assert settings["day"]["led"] == 42.0
    assert settings["day_silent"]["led"] == 65.0
    assert settings["night"]["led"] == 0.0
    assert settings["night_silent"]["led"] == 0.0
    persisted = json.loads((tmp_path / "control.json").read_text())
    assert persisted["led"]["0"]["day"] == 42.0
    assert persisted["led"]["0"]["day_silent"] == 65.0


def test_reset_stage_restores_factory_phase_and_keeps_other_phase(tmp_path, monkeypatch):
    from copy import deepcopy
    from lisa_pro_ui import app as app_module

    factory = [
        {"id": 0, "settings": {"day": {"fan_min": 20, "fan_max": 70, "vpd_min": 0.9, "vpd_max": 1.3, "led": 55}}},
        {"id": 1, "settings": {"day": {"fan_min": 10, "fan_max": 40, "led": 25}}},
    ]

    class FakeClient:
        phases = [
            {"id": 0, "settings": {"day": {"fan_min": 2, "fan_max": 5, "led": 10}}},
            {"id": 1, "settings": {"day": {"fan_min": 12, "fan_max": 35, "led": 30}}},
        ]

        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get_phases(self):
            return {"phases": deepcopy(type(self).phases)}

        def reset_phases(self):
            type(self).phases = deepcopy(factory)

        def set_phases(self, payload):
            type(self).phases = deepcopy(payload["phases"])

    monkeypatch.setattr(app_module, "LisaProClient", FakeClient)
    app = create_app(data_dir=tmp_path, start_controller=False)
    store = app.config["CONTROL_STORE"]
    store.update({"fan_limits": {"0": {"day": {"fan_min": 2, "fan_max": 5}}}, "temperature_limits": {"0": {"max_temp_c": 28}}})

    response = app.test_client().post("/api/control/reset-stage", json={"phase_id": 0})

    assert response.status_code == 200
    body = response.get_json()
    assert FakeClient.phases[0] == factory[0]
    assert FakeClient.phases[1] == {"id": 1, "settings": {"day": {"fan_min": 12, "fan_max": 35, "led": 30}}}
    assert body["config"]["fan_limits"]["0"]["day"] == {"fan_min": 20.0, "fan_max": 70.0}
    assert "0" not in body["config"]["temperature_limits"]
