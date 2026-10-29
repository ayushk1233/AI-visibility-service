from __future__ import annotations

from tests.helpers import (
    ANALYST_TOKEN,
    REVIEWER_TOKEN,
    auth,
    bootstrap,
    decide_report,
    submit_report,
)


def test_missing_and_invalid_tokens_are_rejected(client):
    assert client.get("/audit").status_code == 401
    assert client.get("/audit", headers=auth("wrong-token")).status_code == 401


def test_analyst_cannot_decide_and_reviewer_cannot_edit_or_submit(client):
    ids = bootstrap(client)

    cannot_decide = decide_report(
        client, ids["report_id"], ids["version_id"], token=ANALYST_TOKEN
    )
    assert cannot_decide.status_code == 403
    assert cannot_decide.json()["error"]["code"] == "forbidden"

    cannot_patch = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 1, "findings": []},
        headers=auth(REVIEWER_TOKEN),
    )
    assert cannot_patch.status_code == 403

    cannot_submit = submit_report(client, ids["report_id"], token=REVIEWER_TOKEN)
    assert cannot_submit.status_code == 403


def test_caller_supplied_role_header_is_ignored(client):
    ids = bootstrap(client)
    headers = {**auth(ANALYST_TOKEN), "X-Role": "reviewer", "X-Actor": "reviewer"}
    response = client.post(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}/decision",
        json={"decision": "approve"},
        headers=headers,
    )
    assert response.status_code == 403


def test_reviewer_can_read_but_not_edit(client):
    ids = bootstrap(client)
    assert client.get(f"/reports/{ids['report_id']}", headers=auth(REVIEWER_TOKEN)).status_code == 200
    response = client.patch(
        f"/reports/{ids['report_id']}",
        json={"expected_version_no": 1},
        headers=auth(REVIEWER_TOKEN),
    )
    assert response.status_code == 403
