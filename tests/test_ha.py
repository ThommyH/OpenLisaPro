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
    app = create_app(data_dir=tmp_path, start_controller=False)
    client = app.test_client()
    client.post("/api/control", json={"manual_fan_pct": 63})

    snapshot = client.get("/api/ha/snapshot")
    assert snapshot.status_code == 200
    assert snapshot.get_json()["config"]["manual_fan_pct"] == 63
    assert client.post("/api/ha/heartbeat", json={"node_name": "Backup"}).status_code == 200
    status = client.get("/api/ha").get_json()
    assert status["role"] == "primary"
    assert status["replica_healthy"] is True


def test_replica_rejects_local_and_device_setting_changes(tmp_path, monkeypatch):
    app = replica_app(tmp_path, monkeypatch)
    client = app.test_client()

    control = client.post("/api/control", json={"manual_fan_pct": 99})
    phase = client.post("/api/proxy/phases", json={"phases": []})
    silent = client.post("/api/proxy/silent", json={"enabled": True})

    assert control.status_code == phase.status_code == silent.status_code == 409
    assert app.config["CONTROL_STORE"].get()["manual_fan_pct"] == 40.0


def test_replica_syncs_primary_and_promotes_then_demotes(tmp_path, monkeypatch):
    app = replica_app(tmp_path, monkeypatch)
    controller = app.config["FAN_CONTROLLER"]
    calls = []
    monkeypatch.setattr(controller, "start", lambda: calls.append("start"))
    monkeypatch.setattr(controller, "stop", lambda: calls.append("stop"))
    snapshot = {"role": "primary", "config": {"enabled": False, "manual_fan_pct": 27}}
    requested_urls = []

    def healthy_urlopen(request, timeout):
        requested_urls.append(request.full_url)
        if request.full_url.endswith("/api/ha/snapshot"):
            return FakeResponse(snapshot)
        return FakeResponse({"ok": True})

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", healthy_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is True
    assert app.config["CONTROL_STORE"].get()["manual_fan_pct"] == 27.0
    assert requested_urls == ["http://master:5050/api/ha/snapshot", "http://master:5050/api/ha/heartbeat"]
    assert app.test_client().get("/api/ha").get_json()["role"] == "replica"

    def unavailable_urlopen(*_args, **_kwargs):
        raise OSError("master is down")

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", unavailable_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is False
    assert app.test_client().get("/api/ha").get_json()["role"] == "primary"
    assert calls == ["start"]

    monkeypatch.setattr("lisa_pro_ui.app.urlopen", healthy_urlopen)
    assert app.config["HA_CHECK_MASTER"]() is True
    assert app.test_client().get("/api/ha").get_json()["role"] == "replica"
    assert calls == ["start", "stop"]
