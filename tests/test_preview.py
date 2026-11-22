"""The e-service preview page is served."""

from fastapi.testclient import TestClient

from app.api import app


def test_preview_page_is_served():
    response = TestClient(app).get("/preview")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
