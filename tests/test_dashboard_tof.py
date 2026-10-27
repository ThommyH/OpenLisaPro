from pathlib import Path

from lisa_pro_ui.app import create_app


def test_dashboard_includes_tof_distance_metric_and_german_translation(tmp_path):
    app = create_app(data_dir=tmp_path, start_controller=False)

    response = app.test_client().get("/")

    assert response.status_code == 200
    assert b'ToF distance' in response.data
    assert b'id="tofDistance"' in response.data
    assert b'DLI (approx.)' in response.data
    assert b'id="dliEstimate"' in response.data

    assets = Path(__file__).parents[1] / "src" / "lisa_pro_ui" / "static"
    script = (assets / "js" / "app.js").read_text()
    translations = (assets / "js" / "i18n.js").read_text()
    assert 's.tof_mm == null ? "—"' in script
    assert '"ToF distance": "ToF-Abstand"' in translations
    assert '"DLI (approx.)": "DLI (ca.)"' in translations
