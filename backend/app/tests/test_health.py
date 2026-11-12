from .conftest import TEST_MODEL_TAG


def test_health_reports_ok_when_model_available(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["ollama"]["reachable"] is True
    assert body["ollama"]["error"] is None
    assert body["model"]["tag"] == TEST_MODEL_TAG
    assert body["model"]["available"] is True


def test_health_reports_reachable_but_model_missing(app, client):
    app.state.ollama.models = ["some-other-model:latest"]
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["ollama"]["reachable"] is True
    assert body["model"]["available"] is False
    assert "is not installed" in body["ollama"]["error"]


def test_health_reports_disconnected_ollama(client, fake_ollama):
    fake_ollama.behavior = "unavailable"
    response = client.get("/api/health")
    assert response.status_code == 200  # health endpoint itself stays up
    body = response.json()
    assert body["status"] == "degraded"
    assert body["ollama"]["reachable"] is False
    assert body["ollama"]["error"]
    assert body["model"]["available"] is False
