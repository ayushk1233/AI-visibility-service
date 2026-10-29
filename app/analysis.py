from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any

from .audit import record_audit
from .categories import CATEGORY_TAXONOMY_VERSION, categories_for_prompts
from .config import Settings
from .diagnostics import audit_report_evidence, build_diagnostics
from .errors import AppError
from .llm.prompts import PROMPT_VERSION
from .llm.service import ClassifierService
from .scoring import DEFAULT_WEIGHTS, score_rows
from .util import canonical_json, content_hash, iso, json_loads, new_id, utcnow


def _now_plus(seconds: int) -> str:
    return iso(utcnow() + timedelta(seconds=seconds))


def build_config(conn, dataset_id: str, settings: Settings) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT prompt_id, text FROM prompts WHERE dataset_id = ? ORDER BY prompt_id",
        (dataset_id,),
    ).fetchall()
    prompts = [dict(row) for row in rows]
    return {
        "config_version": "analysis-config-v1",
        "scoring_version": "visibility-v1",
        "scoring": {
            "weights": dict(DEFAULT_WEIGHTS),
            "interval": "wilson-95",
        },
        "label_taxonomy": "kasparro-labels-v1",
        "category_taxonomy": CATEGORY_TAXONOMY_VERSION,
        "classifier_prompt_version": PROMPT_VERSION,
        "mode": settings.llm_mode,
        "model": settings.llm_model,
        "prompt_categories": categories_for_prompts(prompts),
    }


def create_analysis(
    conn,
    settings: Settings,
    dataset_id: str,
    actor: str,
    force: bool = False,
) -> dict[str, Any]:
    dataset = conn.execute(
        "SELECT dataset_id FROM datasets WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    if not dataset:
        raise AppError(404, "dataset_not_found", "Dataset not found")

    config = build_config(conn, dataset_id, settings)
    config_digest = content_hash(config)
    existing = conn.execute(
        """SELECT * FROM analyses
           WHERE dataset_id = ? AND config_hash = ? AND mode = ?
           ORDER BY created_at DESC LIMIT 1""",
        (dataset_id, config_digest, settings.llm_mode),
    ).fetchone()
    if existing and not force:
        raise AppError(
            409,
            "analysis_exists",
            "An analysis with this configuration already exists; use force=true to create another",
            {"analysis_id": existing["analysis_id"], "status": existing["status"]},
        )

    analysis_id = new_id("ana")
    created_at = iso()
    with conn:
        conn.execute(
            """INSERT INTO analyses
               (analysis_id, dataset_id, config_json, config_hash, mode, status, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)""",
            (
                analysis_id,
                dataset_id,
                canonical_json(config),
                config_digest,
                settings.llm_mode,
                actor,
                created_at,
            ),
        )
        record_audit(conn, actor, "analysis.created", "analysis", analysis_id, {"dataset_id": dataset_id})
    row = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    return dict(row)


def recover_interrupted_analyses(db) -> int:
    now = iso()
    with db.connect() as conn:
        cursor = conn.execute(
            """UPDATE analyses
               SET status = 'interrupted', lease_until = NULL
               WHERE status = 'running' AND (lease_until IS NULL OR lease_until < ?)""",
            (now,),
        )
        conn.commit()
        return cursor.rowcount


def resume_analysis(conn, analysis_id: str, actor: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    if not row:
        raise AppError(404, "analysis_not_found", "Analysis not found")
    if row["status"] == "completed":
        raise AppError(409, "analysis_completed", "Completed analysis does not need a resume")
    if row["status"] == "running" and row["lease_until"] and row["lease_until"] >= iso():
        raise AppError(409, "analysis_running", "Analysis is already running")
    with conn:
        conn.execute(
            """UPDATE analyses
               SET status = 'queued', lease_until = NULL, finished_at = NULL, error_json = NULL
               WHERE analysis_id = ?""",
            (analysis_id,),
        )
        record_audit(conn, actor, "analysis.resumed", "analysis", analysis_id)
    updated = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    return dict(updated)


def _claim_analysis(conn, analysis_id: str) -> bool:
    now = iso()
    lease = _now_plus(60)
    cursor = conn.execute(
        """UPDATE analyses
           SET status = 'running',
               started_at = COALESCE(started_at, ?),
               lease_until = ?,
               error_json = NULL
           WHERE analysis_id = ?
             AND (status IN ('queued', 'interrupted')
                  OR (status = 'running' AND (lease_until IS NULL OR lease_until < ?)))""",
        (now, lease, analysis_id, now),
    )
    conn.commit()
    return cursor.rowcount == 1


def _upsert_result(conn, payload: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO analysis_results
           (result_id, analysis_id, response_id, status, reason_code, reason_detail,
            labels_json, evidence_json, raw_output_json, attempt_log_json,
            classifier_version, model_mode, model_name, attempts, latency_ms,
            input_tokens, output_tokens, cost_estimate, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(analysis_id, response_id) DO UPDATE SET
             status = excluded.status,
             reason_code = excluded.reason_code,
             reason_detail = excluded.reason_detail,
             labels_json = excluded.labels_json,
             evidence_json = excluded.evidence_json,
             raw_output_json = excluded.raw_output_json,
             attempt_log_json = excluded.attempt_log_json,
             classifier_version = excluded.classifier_version,
             model_mode = excluded.model_mode,
             model_name = excluded.model_name,
             attempts = excluded.attempts,
             latency_ms = excluded.latency_ms,
             input_tokens = excluded.input_tokens,
             output_tokens = excluded.output_tokens,
             cost_estimate = excluded.cost_estimate,
             updated_at = excluded.updated_at""",
        (
            payload["result_id"],
            payload["analysis_id"],
            payload["response_id"],
            payload["status"],
            payload.get("reason_code"),
            payload.get("reason_detail"),
            payload.get("labels_json"),
            payload.get("evidence_json"),
            payload.get("raw_output_json"),
            payload.get("attempt_log_json"),
            payload.get("classifier_version"),
            payload.get("model_mode"),
            payload.get("model_name"),
            payload.get("attempts", 0),
            payload.get("latency_ms"),
            payload.get("input_tokens"),
            payload.get("output_tokens"),
            payload.get("cost_estimate"),
            payload["created_at"],
            payload["updated_at"],
        ),
    )


def run_analysis(db, settings: Settings, analysis_id: str) -> None:
    conn = db.connect()
    try:
        if not _claim_analysis(conn, analysis_id):
            return
        analysis = conn.execute(
            "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
        if not analysis:
            return
        config = json_loads(analysis["config_json"], {}) or {}
        dataset_id = analysis["dataset_id"]
        prompts = {
            row["prompt_id"]: row["text"]
            for row in conn.execute(
                "SELECT prompt_id, text FROM prompts WHERE dataset_id = ?", (dataset_id,)
            ).fetchall()
        }
        responses = conn.execute(
            """SELECT response_id, prompt_id, answer_text
               FROM responses WHERE dataset_id = ? ORDER BY response_id""",
            (dataset_id,),
        ).fetchall()
        citation_counts = {
            row["response_id"]: row["count"]
            for row in conn.execute(
                """SELECT response_id, COUNT(*) AS count FROM citations
                   WHERE response_id IN (SELECT response_id FROM responses WHERE dataset_id = ?)
                   GROUP BY response_id""",
                (dataset_id,),
            ).fetchall()
        }
        existing = {
            row["response_id"]: row
            for row in conn.execute(
                "SELECT * FROM analysis_results WHERE analysis_id = ?", (analysis_id,)
            ).fetchall()
        }
        service = ClassifierService(settings)
        max_attempts = settings.llm_max_attempts

        for index, response in enumerate(responses, start=1):
            previous = existing.get(response["response_id"])
            if previous:
                if previous["status"] in {"succeeded", "skipped"}:
                    continue
                if previous["status"] == "failed" and (previous["attempts"] or 0) >= max_attempts:
                    continue

            citations = [
                dict(row)
                for row in conn.execute(
                    """SELECT citation_id, "order", citation_type, url, domain, title,
                              source_name, publication_date
                       FROM citations WHERE response_id = ? ORDER BY "order", citation_id""",
                    (response["response_id"],),
                ).fetchall()
            ]
            outcome = service.classify(
                prompts.get(response["prompt_id"], ""),
                response["answer_text"],
                citations,
            )
            now = iso()
            labels = outcome.classification.model_dump(mode="json") if outcome.classification else None
            evidence = labels.get("evidence") if labels else None
            result_id = previous["result_id"] if previous else new_id("res")
            payload = {
                "result_id": result_id,
                "analysis_id": analysis_id,
                "response_id": response["response_id"],
                "status": outcome.status,
                "reason_code": outcome.reason_code,
                "reason_detail": outcome.reason_detail,
                "labels_json": canonical_json(labels) if labels is not None else None,
                "evidence_json": canonical_json(evidence) if evidence is not None else None,
                "raw_output_json": outcome.raw_output,
                "attempt_log_json": canonical_json(outcome.attempt_log),
                "classifier_version": config.get("classifier_prompt_version"),
                "model_mode": outcome.model_mode,
                "model_name": outcome.model_name,
                "attempts": outcome.attempts,
                "latency_ms": outcome.latency_ms,
                "input_tokens": outcome.input_tokens,
                "output_tokens": outcome.output_tokens,
                "cost_estimate": outcome.cost_estimate,
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
            }
            with conn:
                _upsert_result(conn, payload)
                if index % 10 == 0:
                    conn.execute(
                        "UPDATE analyses SET lease_until = ? WHERE analysis_id = ?",
                        (_now_plus(60), analysis_id),
                    )

        counts = Counter(
            row["status"]
            for row in conn.execute(
                "SELECT status FROM analysis_results WHERE analysis_id = ?", (analysis_id,)
            ).fetchall()
        )
        with conn:
            conn.execute(
                """UPDATE analyses
                   SET status = 'completed', finished_at = ?, lease_until = NULL, error_json = NULL
                   WHERE analysis_id = ?""",
                (iso(), analysis_id),
            )
            record_audit(
                conn,
                analysis["created_by"],
                "analysis.completed",
                "analysis",
                analysis_id,
                {
                    "succeeded": counts.get("succeeded", 0),
                    "failed": counts.get("failed", 0),
                    "skipped": counts.get("skipped", 0),
                },
            )
    except Exception as exc:
        with conn:
            conn.execute(
                """UPDATE analyses
                   SET status = 'failed', finished_at = ?, lease_until = NULL, error_json = ?
                   WHERE analysis_id = ?""",
                (iso(), canonical_json({"code": "analysis_failed", "detail": str(exc)}), analysis_id),
            )
        raise
    finally:
        conn.close()


def get_analysis_status(conn, analysis_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)).fetchone()
    if not row:
        raise AppError(404, "analysis_not_found", "Analysis not found")
    eligible = conn.execute(
        "SELECT COUNT(*) AS count FROM responses WHERE dataset_id = ?", (row["dataset_id"],)
    ).fetchone()["count"]
    status_rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM analysis_results WHERE analysis_id = ? GROUP BY status",
        (analysis_id,),
    ).fetchall()
    counts = {item["status"]: item["count"] for item in status_rows}
    completed = sum(counts.values())
    data = dict(row)
    data["config"] = json_loads(row["config_json"], {})
    data["config_json"] = None
    data["error"] = json_loads(row["error_json"], None)
    data["error_json"] = None
    data["counts"] = {
        "eligible": eligible,
        "succeeded": counts.get("succeeded", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
        "pending": max(0, eligible - completed),
    }
    data["coverage"] = round(counts.get("succeeded", 0) / eligible, 4) if eligible else 0.0
    return data


def build_report_content(conn, analysis_id: str) -> dict[str, Any]:
    analysis = conn.execute(
        "SELECT * FROM analyses WHERE analysis_id = ?", (analysis_id,)
    ).fetchone()
    if not analysis:
        raise AppError(404, "analysis_not_found", "Analysis not found")
    config = json_loads(analysis["config_json"], {}) or {}
    dataset = conn.execute(
        "SELECT * FROM datasets WHERE dataset_id = ?", (analysis["dataset_id"],)
    ).fetchone()
    if not dataset:
        raise AppError(404, "dataset_not_found", "Dataset not found")

    response_rows = conn.execute(
        """SELECT r.response_id, r.prompt_id, r.engine, r.engine_name, r.model_version,
                  p.text AS prompt_text
           FROM responses r JOIN prompts p ON p.prompt_id = r.prompt_id
           WHERE r.dataset_id = ? ORDER BY r.response_id""",
        (analysis["dataset_id"],),
    ).fetchall()
    result_rows = {
        row["response_id"]: row
        for row in conn.execute(
            "SELECT * FROM analysis_results WHERE analysis_id = ?", (analysis_id,)
        ).fetchall()
    }
    citation_counts = {
        row["response_id"]: row["count"]
        for row in conn.execute(
            """SELECT response_id, COUNT(*) AS count FROM citations
               WHERE response_id IN (SELECT response_id FROM responses WHERE dataset_id = ?)
               GROUP BY response_id""",
            (analysis["dataset_id"],),
        ).fetchall()
    }
    categories = config.get("prompt_categories", {})

    rows = []
    response_index = []
    for response in response_rows:
        result = result_rows.get(response["response_id"])
        labels = json_loads(result["labels_json"], {}) if result else {}
        evidence = json_loads(result["evidence_json"], []) if result else []
        category = categories.get(response["prompt_id"], "general")
        status = result["status"] if result else "pending"
        row = {
            "response_id": response["response_id"],
            "prompt_id": response["prompt_id"],
            "engine": response["engine"],
            "engine_name": response["engine_name"],
            "model_version": response["model_version"],
            "prompt_category": category,
            "status": status,
            "reason_code": result["reason_code"] if result else None,
            "labels": labels or {},
            "evidence": evidence or [],
            "citation_count": citation_counts.get(response["response_id"], 0),
        }
        rows.append(row)
        response_index.append(
            {
                "response_id": row["response_id"],
                "engine": row["engine"],
                "prompt_category": category,
                "status": status,
                "present": bool(labels.get("kasparro_present")),
                "recommended": bool(labels.get("recommended")),
                "negative": bool(labels.get("negatively_characterised")),
                "citation_count": row["citation_count"],
            }
        )

    weights = (config.get("scoring") or {}).get("weights") or DEFAULT_WEIGHTS
    scores = score_rows(rows, eligible=len(response_rows), weights=weights)
    diagnostics, remedies = build_diagnostics(scores, rows)
    content = {
        "schema_version": "report-content-v1",
        "run": json_loads(dataset["run_json"], {}) or {},
        "analysis": {
            "analysis_id": analysis_id,
            "mode": analysis["mode"],
            "model": config.get("model"),
            "config_hash": analysis["config_hash"],
            "scoring_version": config.get("scoring_version"),
            "category_taxonomy": config.get("category_taxonomy"),
            "classifier_prompt_version": config.get("classifier_prompt_version"),
        },
        "coverage": {
            "eligible": scores["eligible"],
            "succeeded": scores["n"],
            "failed": scores["failed"],
            "skipped": scores["skipped"],
            "pending": max(0, scores["eligible"] - scores["n"] - scores["failed"] - scores["skipped"]),
            "coverage": scores["coverage"],
        },
        "scores": {
            "overall": {
                key: value
                for key, value in scores.items()
                if key not in {"by_engine", "by_category"}
            },
            "by_engine": scores["by_engine"],
            "by_category": scores["by_category"],
        },
        "diagnostics": diagnostics,
        "remedies": remedies,
        "response_index": response_index,
        "generated_at": iso(),
    }
    answer_text_by_id = {
        row["response_id"]: row["answer_text"]
        for row in conn.execute(
            "SELECT response_id, answer_text FROM responses WHERE dataset_id = ?",
            (analysis["dataset_id"],),
        ).fetchall()
    }
    errors = audit_report_evidence(content, answer_text_by_id)
    content["evidence_audit"] = {"ok": not errors, "errors": errors}
    return content
