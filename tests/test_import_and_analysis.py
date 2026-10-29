from __future__ import annotations

from datetime import timedelta

from app.analysis import recover_interrupted_analyses, resume_analysis, run_analysis
from app.util import iso, utcnow
from tests.helpers import (
    auth,
    create_report,
    default_rows,
    import_pack,
    start_analysis,
    write_pack,
)


def test_duplicate_import_is_idempotent_and_modified_content_conflicts(client):
    first = import_pack(client)
    second = import_pack(client)
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert first.json()["dataset"]["dataset_id"] == second.json()["dataset"]["dataset_id"]
    assert second.json()["counts"] == first.json()["counts"]


def test_modified_content_with_same_run_id_conflicts(client, pack_dir):
    assert import_pack(client).status_code == 201
    _, responses, _ = default_rows()
    responses[0]["answer_text"] = responses[0]["answer_text"] + " Updated."
    write_pack(pack_dir, responses=responses)

    response = import_pack(client)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_conflict"


def test_analysis_retry_does_not_duplicate_results(client):
    imported = import_pack(client)
    dataset_id = imported.json()["dataset"]["dataset_id"]
    analysis = start_analysis(client, dataset_id)
    assert analysis.status_code == 202
    body = analysis.json()
    analysis_id = body["analysis_id"]
    assert body["status"] == "completed"
    assert body["counts"]["succeeded"] == 6

    duplicate = start_analysis(client, dataset_id)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "analysis_exists"

    settings = client.app.state.settings
    run_analysis(client.app.state.db, settings, analysis_id)
    current = client.get(f"/analyses/{analysis_id}", headers=auth())
    assert current.status_code == 200
    assert current.json()["counts"]["succeeded"] == 6
    assert current.json()["counts"]["pending"] == 0


def test_restart_and_resume_reuses_results(client, test_settings):
    imported = import_pack(client)
    dataset_id = imported.json()["dataset"]["dataset_id"]
    analysis_id = start_analysis(client, dataset_id).json()["analysis_id"]
    db = client.app.state.db

    with db.connect() as conn:
        with conn:
            conn.execute(
                "DELETE FROM analysis_results WHERE analysis_id = ? AND response_id = ?",
                (analysis_id, "resp-p1-gemini"),
            )
            conn.execute(
                """UPDATE analysis_results
                   SET status = 'failed', reason_code = 'timeout', attempts = 1
                   WHERE analysis_id = ? AND response_id = ?""",
                (analysis_id, "resp-p2-chatgpt"),
            )
            conn.execute(
                "UPDATE analyses SET status = 'running', lease_until = ? WHERE analysis_id = ?",
                (iso(utcnow() - timedelta(minutes=5)), analysis_id),
            )

    assert recover_interrupted_analyses(db) == 1
    with db.connect() as conn:
        assert conn.execute(
            "SELECT status FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()["status"] == "interrupted"
        resume_analysis(conn, analysis_id, "analyst")

    run_analysis(db, test_settings, analysis_id)
    with db.connect() as conn:
        status = conn.execute(
            "SELECT status FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()["status"]
        rows = conn.execute(
            "SELECT response_id, status FROM analysis_results WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchall()
    assert status == "completed"
    assert len(rows) == 6
    assert len({row["response_id"] for row in rows}) == 6
    assert all(row["status"] == "succeeded" for row in rows)


def test_recovery_does_not_interrupt_a_live_lease(client):
    imported = import_pack(client)
    dataset_id = imported.json()["dataset"]["dataset_id"]
    analysis_id = start_analysis(client, dataset_id).json()["analysis_id"]
    db = client.app.state.db
    with db.connect() as conn:
        with conn:
            conn.execute(
                "UPDATE analyses SET status = 'running', lease_until = ? WHERE analysis_id = ?",
                (iso(utcnow() + timedelta(minutes=5)), analysis_id),
            )
    assert recover_interrupted_analyses(db) == 0
    with db.connect() as conn:
        assert conn.execute(
            "SELECT status FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()["status"] == "running"


def test_whitespace_answer_is_skipped_not_scored_as_zero(client, pack_dir):
    _, responses, _ = default_rows()
    responses[0]["answer_text"] = "   "
    write_pack(pack_dir, responses=responses)
    imported = import_pack(client)
    dataset_id = imported.json()["dataset"]["dataset_id"]
    analysis = start_analysis(client, dataset_id)
    assert analysis.status_code == 202
    body = analysis.json()
    assert body["counts"]["skipped"] == 1
    assert body["counts"]["succeeded"] == 5
    assert body["counts"]["failed"] == 0
    assert body["coverage"] == 0.8333

    report = create_report(client, body["analysis_id"])
    assert report.status_code == 201, report.text
    version = report.json()["versions"][0]["version_id"]
    detail = client.get(f"/reports/{report.json()['report_id']}/versions/{version}", headers=auth())
    content = detail.json()["content"]
    assert content["coverage"]["skipped"] == 1
    assert content["scores"]["overall"]["skipped"] == 1
    assert content["scores"]["overall"]["n"] == 5
