from __future__ import annotations

from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from fastapi.testclient import TestClient

from app.errors import AppError
from app.main import create_app
from app.reports import decide_report as decide_report_fn
from app.util import iso, utcnow
from tests.helpers import (
    auth,
    bootstrap,
    create_report,
    decide_report,
    submit_report,
)


def _export(client, report_id, version_id=None, token="analyst-token"):
    if version_id:
        return client.get(f"/reports/{report_id}/versions/{version_id}/export", headers=auth(token))
    return client.get(f"/reports/{report_id}/export", headers=auth(token))


def test_draft_submitted_and_rejected_versions_cannot_export(client):
    ids = bootstrap(client)
    draft = _export(client, ids["report_id"], ids["version_id"])
    assert draft.status_code == 409
    assert draft.json()["error"]["code"] == "not_approved"

    assert submit_report(client, ids["report_id"]).status_code == 200
    submitted = _export(client, ids["report_id"], ids["version_id"])
    assert submitted.status_code == 409
    assert submitted.json()["error"]["code"] == "not_approved"

    rejected = decide_report(
        client, ids["report_id"], ids["version_id"], "reject", "Needs a clearer remedy."
    )
    assert rejected.status_code == 200
    assert rejected.json()["state"] == "rejected"
    assert _export(client, ids["report_id"], ids["version_id"]).status_code == 409


def test_approved_version_exports_and_new_draft_is_not_authorised(client):
    ids = bootstrap(client)
    assert submit_report(client, ids["report_id"]).status_code == 200
    approved = decide_report(client, ids["report_id"], ids["version_id"], "approve")
    assert approved.status_code == 200
    assert approved.json()["state"] == "approved"

    exported = _export(client, ids["report_id"], ids["version_id"])
    assert exported.status_code == 200
    content = exported.json()["content"]
    assert len(content["diagnostics"]) == 3
    assert len(content["remedies"]) == 3
    assert content["evidence_audit"]["ok"] is True

    original = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()["content"]
    content = {**original, "analyst_note": "Draft change for re-review."}
    edits = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 1, "content": content},
        headers=auth(),
    )
    assert edits.status_code == 200
    new_draft = edits.json()
    assert new_draft["version_no"] == 2
    assert new_draft["state"] == "draft"

    assert _export(client, ids["report_id"], new_draft["version_id"]).status_code == 409
    old = _export(client, ids["report_id"], ids["version_id"])
    assert old.status_code == 200
    assert old.json()["version"]["state"] == "approved"


def test_version_from_another_report_is_rejected(client):
    first = bootstrap(client)
    second = create_report(client, first["analysis_id"])
    assert second.status_code == 201
    second_version = second.json()["versions"][0]["version_id"]
    response = _export(client, first["report_id"], second_version)
    assert response.status_code == 404


def test_review_expiry_refuses_decision_and_export_and_survives_restart(client, test_settings):
    ids = bootstrap(client)
    assert submit_report(client, ids["report_id"]).status_code == 200
    db = client.app.state.db
    with db.connect() as conn:
        with conn:
            conn.execute(
                "UPDATE report_versions SET expires_at = ? WHERE version_id = ?",
                (iso(utcnow() - timedelta(minutes=1)), ids["version_id"]),
            )

    expired = decide_report(client, ids["report_id"], ids["version_id"], "approve")
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "review_expired"
    assert _export(client, ids["report_id"], ids["version_id"]).status_code == 409

    with TestClient(create_app(test_settings)) as restarted:
        state = restarted.get(
            f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
        ).json()["state"]
    assert state == "expired"


def test_approval_expiry_refuses_export(client):
    ids = bootstrap(client)
    assert submit_report(client, ids["report_id"]).status_code == 200
    assert decide_report(client, ids["report_id"], ids["version_id"], "approve").status_code == 200

    with client.app.state.db.connect() as conn:
        with conn:
            conn.execute(
                "UPDATE report_versions SET expires_at = ? WHERE version_id = ?",
                (iso(utcnow() - timedelta(minutes=1)), ids["version_id"]),
            )
    response = _export(client, ids["report_id"], ids["version_id"])
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_expired"


def test_repeated_decision_does_not_overwrite_first(client):
    ids = bootstrap(client)
    assert submit_report(client, ids["report_id"]).status_code == 200
    first = decide_report(client, ids["report_id"], ids["version_id"], "approve")
    assert first.status_code == 200

    repeated = decide_report(client, ids["report_id"], ids["version_id"], "approve")
    rejected = decide_report(client, ids["report_id"], ids["version_id"], "reject", "Too late.")
    assert repeated.status_code == 409
    assert rejected.status_code == 409
    version = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    assert version["state"] == "approved"
    assert version["decision"] == "approve"


def test_concurrent_decisions_have_one_winner(client):
    ids = bootstrap(client)
    assert submit_report(client, ids["report_id"]).status_code == 200
    db = client.app.state.db
    barrier = Barrier(2)

    def worker():
        conn = db.connect()
        try:
            barrier.wait(timeout=5)
            try:
                result = decide_report_fn(
                    conn,
                    ids["report_id"],
                    ids["version_id"],
                    "approve",
                    "Concurrent approval.",
                    "reviewer",
                )
                return ("ok", result)
            except AppError as exc:
                return ("error", exc)
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: worker(), range(2)))

    winners = [item for item in results if item[0] == "ok"]
    losers = [item for item in results if item[0] == "error"]
    assert len(winners) == 1
    assert len(losers) == 1
    assert losers[0][1].status_code == 409
    version = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    assert version["state"] == "approved"


def test_stale_edit_is_rejected_without_partial_write(client):
    ids = bootstrap(client)
    before = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    stale = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 99, "findings": [{"id": "bad"}]},
        headers=auth(),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "stale_version"

    after = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    assert after["content_hash"] == before["content_hash"]
    assert after["content"] == before["content"]

    content = {**before["content"], "analyst_note": "Edited."}
    updated = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 1, "content": content},
        headers=auth(),
    )
    assert updated.status_code == 200
    assert updated.json()["version_no"] == 1
    assert updated.json()["content_hash"] != before["content_hash"]


def test_patch_with_invalid_evidence_is_rejected_without_write(client):
    ids = bootstrap(client)
    before = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    content = {**before["content"]}
    content["diagnostics"] = [dict(item) for item in content["diagnostics"]]
    content["diagnostics"][0] = {
        **content["diagnostics"][0],
        "evidence": [
            {**content["diagnostics"][0]["evidence"][0], "quote": "Quote not in answer."}
        ],
    }
    response = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 1, "content": content},
        headers=auth(),
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "evidence_audit_failed"
    after = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    assert after["content_hash"] == before["content_hash"]
