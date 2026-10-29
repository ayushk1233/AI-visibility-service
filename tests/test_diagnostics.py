from __future__ import annotations

from copy import deepcopy

from app.diagnostics import audit_report_evidence
from tests.helpers import auth, bootstrap


def _content(client):
    ids = bootstrap(client)
    version = client.get(
        f"/reports/{ids['report_id']}/versions/{ids['version_id']}", headers=auth()
    ).json()
    return ids, version["content"]


def test_generated_report_evidence_audit_passes(client):
    _, content = _content(client)
    assert content["evidence_audit"]["ok"] is True
    assert len(content["diagnostics"]) == 3
    assert len(content["remedies"]) == 3


def test_incorrect_evidence_quote_is_rejected(client):
    _, content = _content(client)
    broken = deepcopy(content)
    response_id = broken["diagnostics"][0]["evidence"][0]["response_id"]
    broken["diagnostics"][0]["evidence"][0]["quote"] = "This quote does not occur."
    texts = {row["response_id"]: "irrelevant" for row in broken["response_index"]}
    with client.app.state.db.connect() as conn:
        texts[response_id] = conn.execute(
            "SELECT answer_text FROM responses WHERE response_id = ?", (response_id,)
        ).fetchone()["answer_text"]
    errors = audit_report_evidence(broken, texts)
    assert any("not present" in error for error in errors)


def test_unknown_response_is_rejected(client):
    _, content = _content(client)
    broken = deepcopy(content)
    broken["diagnostics"][0]["evidence"][0]["response_id"] = "missing-response"
    texts = {row["response_id"]: "text" for row in broken["response_index"]}
    errors = audit_report_evidence(broken, texts)
    assert any("unknown response" in error for error in errors)


def test_unsupported_claim_without_evidence_is_rejected(client):
    _, content = _content(client)
    broken = deepcopy(content)
    broken["diagnostics"][0]["title"] = "Engine weakness"
    broken["diagnostics"][0]["evidence"] = []
    texts = {row["response_id"]: "text" for row in broken["response_index"]}
    errors = audit_report_evidence(broken, texts)
    assert any("no supporting response evidence" in error for error in errors)


def test_wrong_score_counts_are_rejected(client):
    _, content = _content(client)
    broken = deepcopy(content)
    broken["scores"]["overall"]["label_counts"]["present"] = 999
    texts = {row["response_id"]: "text" for row in broken["response_index"]}
    errors = audit_report_evidence(broken, texts)
    assert any("does not match response_index" in error for error in errors)
