from app.core.config import settings


def test_root_returns_service_metadata(client):
    response = client.get("/")
    assert response.status_code == 200

    body = response.json()
    assert body["service"] == settings.PROJECT_NAME
    assert body["version"] == settings.VERSION


def test_health_reports_ok(client):
    response = client.get(f"{settings.API_V1_PREFIX}/health")
    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["service"] == settings.PROJECT_NAME
    assert "timestamp" in body
