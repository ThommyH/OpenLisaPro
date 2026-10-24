import json

from lisa_pro_ui.app import create_app


class FakeResponse:
    def __init__(self, body=None):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.body).encode()


def replica_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LISA_PRO_HA_ROLE", "replica")
    monkeypatch.setenv("LISA_PRO_HA_MASTER_URL", "http://master:5050")
    return create_app(data_dir=tmp_path, start_controller=False)


def test_primary_exposes_config_snapshot_and_tracks_replica_heartbeat(tmp_path, monkeypatch):
    monkeypatch.delenv("LISA_PRO_HA_ROLE", raising=False)
    monkeypatch.setenv("LISA_PRO_HA_PUBLIC_URL", "http://192.168.178.69:5050")
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()
    client.post("/api/control", json={"manual_fan_pct": 63})

    snapshot = client.get("/api/ha/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.get_json()["config"]["manual_fan_pct"] == 63
    assert client.post("/api/ha/heartbeat", json={
        "node_name": "Backup", "node_url": "http://192.168.178.142:5050",
    }).status_code == 200
    status = client.get("/api/ha").get_json()
    assert status["role"] == "primary"
    assert status["node_url"] == "http://192.168.178.69:5050"
    assert status["replica_healthy"] is True
    assert status["replica_url"] == "http://192.168.178.142:5050"


def test_replica_rejects_local_and_device_setting_changes(tmp_path, monkeypatch):
    app = replica_app(tmp_path, monkeypatch)
    client = app.test_client()

    control = client.post("/api/control", json={"manual_fan_pct": 99})
    phase = client.post("/api/proxy/phases", json={"phases": []})
    silent = client.post("/api/proxy/silent", json={"enabled": True})

    assert control.status_code == phase.status_code == silent.status_code == 409
    assert app.config["CONTROL_STORE"].get()["manual_fan_pct"] == 40.0


def test_replica_syncs_primary_and_promotes_then_demotes(tmp_path, monkeypatch):
    monkeypatch.setenv("LISA_PRO_HA_PUBLIC_URL", "http://192.168.178.142:5050")
    app = replica_app(tmp_path, monkeypatch)
    controller = app.config["FAN_CONTROLLER"]
    calls = []
    applied_configs = []
    monkeypatch.setattr(controller, "start", lambda: calls.append("start"))
    monkeypatch.setattr(controller, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(
        controller,
        "sync_configured_settings",
        lambda _phases, cfg, **_kwargs: applied_configs.append(cfg.copy()),
    )
    primary_config = {
        "enabled": False,
        "manual_fan_pct": 27,
        "fan_limits": {"0": {"day": {"fan_min": 24, "fan_max": 62}}},
    }
    snapshot = {"role": "primary", "config": primary_config}
    requested_urls = []
    healthy_requests = []

    def healthy_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        healthy_requests.append(request)
        if request.full_url.endswith("/api/ha/snapshot"):
            return FakeResponse(snapshot)
        return FakeResponse({"ok": True})

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", healthy_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is True
    assert app.config["CONTROL_STORE"].get()["manual_fan_pct"] == 27.0
    assert requested_urls == ["http://master:5050/api/ha/snapshot", "http://master:5050/api/ha/heartbeat"]
    heartbeat_payload = json.loads(healthy_requests[1].data.decode())
    assert heartbeat_payload == {"node_name": "OpenLisaPro", "node_url": "http://192.168.178.142:5050"}
    assert app.test_client().get("/api/ha").get_json()["role"] == "replica"

    def unavailable_urlopen(*_args, **_kwargs):
        raise OSError("master is down")

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", unavailable_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is False
    assert app.test_client().get("/api/ha").get_json()["role"] == "primary"
    assert calls == ["start"]

    # Settings changed while this replica was promoted must not survive failback.
    app.config["CONTROL_STORE"].update({
        "manual_fan_pct": 91,
        "fan_limits": {"0": {"day": {"fan_min": 5, "fan_max": 99}}},
    })

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", healthy_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is True
    assert app.test_client().get("/api/ha").get_json()["role"] == "replica"
    assert calls == ["start", "stop"]
    restored = app.config["CONTROL_STORE"].get()
    assert restored["manual_fan_pct"] == 27.0
    assert restored["fan_limits"]["0"]["day"] == {"fan_min": 24.0, "fan_max": 62.0}
    assert applied_configs[-1]["manual_fan_pct"] == 27.0
    assert applied_configs[-1]["fan_limits"]["0"]["day"] == {"fan_min": 24.0, "fan_max": 62.0}

    # Every healthy poll remains authoritative even if local state drifts later.
    app.config["CONTROL_STORE"].update({"manual_fan_pct": 44})
    assert app.config["HA_CHECK_MASTER"]() is True
    assert app.config["CONTROL_STORE"].get()["manual_fan_pct"] == 27.0
