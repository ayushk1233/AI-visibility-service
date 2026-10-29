from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.main import create_app
from tests.helpers import auth, bootstrap, import_pack


def test_health_root_and_docs_are_available(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["database"] == "ok"

    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["health"] == "/health"

    docs = client.get("/openapi.json")
    assert docs.status_code == 200
    assert "/reports/{report_id}/export" in docs.json()["paths"]


def test_structured_errors_and_request_id(client):
    response = client.get("/reports/missing", headers=auth())
    assert response.status_code == 404
    body = response.json()
    assert set(body["error"]) == {"code", "message", "details", "request_id"}
    assert body["error"]["code"] == "report_not_found"
    assert response.headers["X-Request-ID"]

    echoed = client.get("/health", headers={"X-Request-ID": "fixed-request-id"})
    assert echoed.headers["X-Request-ID"] == "fixed-request-id"


def test_validation_error_contract_and_pagination_bounds(client):
    invalid = client.post("/datasets", json={"source_dir": 123}, headers=auth())
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"

    assert client.get("/audit?limit=0", headers=auth()).status_code == 422
    assert client.get("/audit?limit=101", headers=auth()).status_code == 422
    page = client.get("/audit?limit=2&offset=0", headers=auth())
    assert page.status_code == 200
    assert page.json()["limit"] == 2
    assert page.json()["offset"] == 0


def test_status_codes_and_stable_ids(client):
    first = import_pack(client)
    second = import_pack(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["dataset"]["dataset_id"] == second.json()["dataset"]["dataset_id"]

    ids = bootstrap(client)
    assert ids["dataset_id"] == first.json()["dataset"]["dataset_id"]
    report = client.get(f"/reports/{ids['report_id']}", headers=auth())
    assert report.status_code == 200
    assert report.json()["versions"][0]["version_id"] == ids["version_id"]


def test_oversized_body_is_rejected_before_routing(test_settings):
    settings = replace(test_settings, max_upload_bytes=64)
    with TestClient(create_app(settings)) as limited:
        response = limited.post(
            "/datasets",
            content=b"x" * 65,
            headers={"Content-Type": "application/json", **auth()},
        )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["X-Request-ID"]
