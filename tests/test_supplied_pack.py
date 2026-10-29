from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from app.config import BASE_DIR
from app.main import create_app
from tests.helpers import auth


def test_supplied_pack_end_to_end(tmp_path, test_settings):
    settings = replace(
        test_settings,
        data_dir=BASE_DIR,
        database_path=tmp_path / "supplied.db",
        sync_analysis=True,
    )
    with TestClient(create_app(settings)) as client:
        imported = client.post("/datasets", json={"source_dir": "."}, headers=auth())
        assert imported.status_code == 201, imported.text
        assert imported.json()["counts"] == {"prompts": 50, "responses": 150, "citations": 2439}
        dataset_id = imported.json()["dataset"]["dataset_id"]

        analysis = client.post(
            "/analyses", json={"dataset_id": dataset_id}, headers=auth()
        )
        assert analysis.status_code == 202, analysis.text
        body = analysis.json()
        assert body["status"] == "completed"
        assert body["counts"] == {
            "eligible": 150,
            "succeeded": 150,
            "failed": 0,
            "skipped": 0,
            "pending": 0,
        }
        assert body["coverage"] == 1.0

        report = client.post(
            "/reports", json={"analysis_id": body["analysis_id"]}, headers=auth()
        )
        assert report.status_code == 201, report.text
        report_id = report.json()["report_id"]
        version_id = report.json()["versions"][0]["version_id"]
        version = client.get(
            f"/reports/{report_id}/versions/{version_id}", headers=auth()
        )
        assert version.status_code == 200
        content = version.json()["content"]
        assert content["evidence_audit"]["ok"] is True
        assert len(content["diagnostics"]) == 3
        assert len(content["remedies"]) == 3
        assert content["scores"]["overall"]["n"] == 150
        assert content["scores"]["overall"]["failed"] == 0
        assert content["scores"]["overall"]["skipped"] == 0
        assert content["run"]["run_id"] == "4a4b71d9-9b0d-4497-85f1-7db5c4e21472"
