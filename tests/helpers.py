from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUN_ID = "run-test-001"
ANALYST_TOKEN = "analyst-token"
REVIEWER_TOKEN = "reviewer-token"


def auth(token: str = ANALYST_TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _response(
    response_id: str,
    prompt_id: str,
    engine: str,
    answer: str,
    model_version: str | None,
    *,
    force_web_search: bool = False,
) -> dict[str, Any]:
    return {
        "response_id": response_id,
        "run_id": RUN_ID,
        "prompt_id": prompt_id,
        "engine": engine,
        "engine_name": {
            "aimode": "Google AI Mode",
            "chatgpt": "ChatGPT",
            "gemini": "Gemini",
        }[engine],
        "model_version": model_version,
        "answer_text": answer,
        "answer_markdown": answer,
        "capture_status": "captured",
        "provider_timestamp": "2026-07-31T00:00:00Z",
        "archive_stored_at": "2026-07-31T00:00:00Z",
        "settings": {"force_web_search": force_web_search},
        "capture_route": "test",
        "provider_api_version": None,
        "provider_task_id": None,
    }


def default_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    prompts = [
        {
            "prompt_id": "prompt-1",
            "run_id": RUN_ID,
            "text": "Which AI visibility tools are worth using for a B2B SaaS brand?",
        },
        {
            "prompt_id": "prompt-2",
            "run_id": RUN_ID,
            "text": "Is Kasparro legit and how much does it cost?",
        },
    ]
    responses = [
        _response(
            "resp-p1-aimode",
            "prompt-1",
            "aimode",
            "Kasparro is a strong option for AI visibility tracking. It helps teams measure AI answers.",
            None,
        ),
        _response(
            "resp-p1-chatgpt",
            "prompt-1",
            "chatgpt",
            "Kasparro is mentioned, but it is not a reliable option and is overpriced.",
            "gpt-5-5",
            force_web_search=True,
        ),
        _response(
            "resp-p1-gemini",
            "prompt-1",
            "gemini",
            "Profound and Peec AI are visibility platforms.",
            "3.5 Flash-Lite",
        ),
        _response(
            "resp-p2-aimode",
            "prompt-2",
            "aimode",
            "Kasparro can help track brand answers, but pricing is unclear.",
            None,
        ),
        _response(
            "resp-p2-chatgpt",
            "prompt-2",
            "chatgpt",
            "Profound is a common choice; there is no clear public pricing.",
            "gpt-5-5",
            force_web_search=True,
        ),
        _response(
            "resp-p2-gemini",
            "prompt-2",
            "gemini",
            "Kasparro is not a scam, but it is expensive and hard to cancel.",
            "3.5 Flash-Lite",
        ),
    ]
    citations = [
        {
            "citation_id": "cit-1",
            "response_id": "resp-p1-aimode",
            "prompt_id": "prompt-1",
            "engine": "aimode",
            "order": 1,
            "citation_type": "inline_hyperlink",
            "url": "https://example.com/kasparro",
            "domain": "example.com",
            "title": "Kasparro overview",
            "source_name": "Example",
            "publication_date": "2026-07-01",
        },
        {
            "citation_id": "cit-2",
            "response_id": "resp-p2-aimode",
            "prompt_id": "prompt-2",
            "engine": "aimode",
            "order": 1,
            "citation_type": "sources_panel",
            "url": "https://example.org/review",
            "domain": "example.org",
            "title": "Independent review",
            "source_name": "Example Org",
            "publication_date": None,
        },
    ]
    return prompts, responses, citations


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def write_pack(
    root: Path,
    *,
    prompts: list[dict[str, Any]] | None = None,
    responses: list[dict[str, Any]] | None = None,
    citations: list[dict[str, Any]] | None = None,
    run: dict[str, Any] | None = None,
    nested: bool = False,
) -> Path:
    default_prompts, default_responses, default_citations = default_rows()
    prompts = default_prompts if prompts is None else prompts
    responses = default_responses if responses is None else responses
    citations = default_citations if citations is None else citations
    run = run or {
        "run_id": RUN_ID,
        "captured_at": "2026-07-31T00:00:00Z",
        "market": "India",
        "language": "English",
    }
    data_dir = root / "data" if nested else root
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "run.json").write_text(json.dumps(run), encoding="utf-8")
    write_jsonl(data_dir / "prompts.jsonl", prompts)
    write_jsonl(data_dir / "responses.jsonl", responses)
    write_jsonl(data_dir / "citations.jsonl", citations)
    return root


def import_pack(client, source_dir: str = "pack", token: str = ANALYST_TOKEN):
    return client.post("/datasets", json={"source_dir": source_dir}, headers=auth(token))


def start_analysis(client, dataset_id: str, token: str = ANALYST_TOKEN):
    return client.post(
        "/analyses",
        json={"dataset_id": dataset_id},
        headers=auth(token),
    )


def create_report(client, analysis_id: str, token: str = ANALYST_TOKEN):
    return client.post(
        "/reports",
        json={"analysis_id": analysis_id},
        headers=auth(token),
    )


def submit_report(client, report_id: str, token: str = ANALYST_TOKEN):
    return client.post(f"/reports/{report_id}/submit", headers=auth(token))


def decide_report(
    client,
    report_id: str,
    version_id: str,
    decision: str = "approve",
    comment: str | None = None,
    token: str = REVIEWER_TOKEN,
):
    body: dict[str, Any] = {"decision": decision}
    if comment is not None:
        body["comment"] = comment
    return client.post(
        f"/reports/{report_id}/versions/{version_id}/decision",
        json=body,
        headers=auth(token),
    )


def bootstrap(client) -> dict[str, str]:
    imported = import_pack(client)
    assert imported.status_code in (200, 201), imported.text
    dataset_id = imported.json()["dataset"]["dataset_id"]
    analysis = start_analysis(client, dataset_id)
    assert analysis.status_code == 202, analysis.text
    analysis_id = analysis.json()["analysis_id"]
    report = create_report(client, analysis_id)
    assert report.status_code == 201, report.text
    body = report.json()
    return {
        "dataset_id": dataset_id,
        "analysis_id": analysis_id,
        "report_id": body["report_id"],
        "version_id": body["versions"][0]["version_id"],
    }
