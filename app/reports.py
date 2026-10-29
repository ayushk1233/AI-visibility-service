from __future__ import annotations

import sqlite3
from datetime import timedelta
from typing import Any

from .analysis import build_report_content, get_analysis_status
from .diagnostics import audit_report_evidence
from .audit import record_audit
from .errors import AppError
from .util import canonical_json, content_hash, iso, is_expired, json_loads, new_id, utcnow


def _version_summary(row) -> dict[str, Any]:
    return {
        "version_id": row["version_id"],
        "version_no": row["version_no"],
        "parent_version_id": row["parent_version_id"],
        "state": row["state"],
        "content_hash": row["content_hash"],
        "analysis_config_hash": row["analysis_config_hash"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "submitted_by": row["submitted_by"],
        "submitted_at": row["submitted_at"],
        "decided_by": row["decided_by"],
        "decided_at": row["decided_at"],
        "decision": row["decision"],
        "decision_comment": row["decision_comment"],
        "expires_at": row["expires_at"],
    }


def _version_detail(row) -> dict[str, Any]:
    data = _version_summary(row)
    data["content"] = json_loads(row["content_json"], {}) or {}
    return data


def _audit_content(conn, report_id: str, content: dict[str, Any]) -> None:
    report = conn.execute(
        "SELECT analysis_id FROM reports WHERE report_id = ?", (report_id,)
    ).fetchone()
    if not report:
        raise AppError(404, "report_not_found", "Report not found")
    texts = {
        row["response_id"]: row["answer_text"]
        for row in conn.execute(
            """SELECT r.response_id, r.answer_text
               FROM responses r JOIN analyses a ON a.dataset_id = r.dataset_id
               WHERE a.analysis_id = ?""",
            (report["analysis_id"],),
        ).fetchall()
    }
    errors = audit_report_evidence(content, texts)
    if errors:
        raise AppError(
            422,
            "evidence_audit_failed",
            "Report content failed the deterministic evidence audit",
            errors[:20],
        )


def create_report(conn, analysis_id: str, title: str, actor: str) -> dict[str, Any]:
    status = get_analysis_status(conn, analysis_id)
    if status["status"] != "completed":
        raise AppError(409, "analysis_incomplete", "Analysis must be completed before creating a report")
    if status["counts"]["pending"] > 0:
        raise AppError(409, "analysis_incomplete", "Analysis still has pending responses")

    content = build_report_content(conn, analysis_id)
    if not content["evidence_audit"]["ok"]:
        raise AppError(
            500,
            "evidence_audit_failed",
            "Report evidence failed the deterministic audit",
            content["evidence_audit"]["errors"][:10],
        )

    report_id = new_id("rpt")
    version_id = new_id("ver")
    now = iso()
    digest = content_hash(content)
    with conn:
        conn.execute(
            """INSERT INTO reports
               (report_id, analysis_id, title, created_by, created_at, current_draft_version)
               VALUES (?, ?, ?, ?, ?, 1)""",
            (report_id, analysis_id, title, actor, now),
        )
        conn.execute(
            """INSERT INTO report_versions
               (version_id, report_id, version_no, parent_version_id, state, content_json,
                content_hash, analysis_config_hash, created_by, created_at)
               VALUES (?, ?, 1, NULL, 'draft', ?, ?, ?, ?, ?)""",
            (
                version_id,
                report_id,
                canonical_json(content),
                digest,
                status["config_hash"],
                actor,
                now,
            ),
        )
        record_audit(conn, actor, "report.created", "report", report_id, {"analysis_id": analysis_id})
    return get_report(conn, report_id)


def get_report(conn, report_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if not row:
        raise AppError(404, "report_not_found", "Report not found")
    versions = conn.execute(
        """SELECT * FROM report_versions WHERE report_id = ?
           ORDER BY version_no DESC LIMIT 100""",
        (report_id,),
    ).fetchall()
    data = dict(row)
    data["versions"] = [_version_summary(version) for version in versions]
    return data


def get_version(conn, report_id: str, version_id: str) -> dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM report_versions WHERE version_id = ? AND report_id = ?",
        (version_id, report_id),
    ).fetchone()
    if not row:
        raise AppError(404, "version_not_found", "Report version not found")
    return _version_detail(row)


def list_versions(conn, report_id: str, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    report = conn.execute("SELECT report_id FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if not report:
        raise AppError(404, "report_not_found", "Report not found")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    rows = conn.execute(
        """SELECT * FROM report_versions WHERE report_id = ?
           ORDER BY version_no DESC LIMIT ? OFFSET ?""",
        (report_id, limit, offset),
    ).fetchall()
    total = conn.execute(
        "SELECT COUNT(*) AS count FROM report_versions WHERE report_id = ?", (report_id,)
    ).fetchone()["count"]
    return {
        "items": [_version_summary(row) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _apply_patch(content: dict[str, Any], patch) -> dict[str, Any]:
    updated = dict(content)
    if patch.content is not None:
        if not isinstance(patch.content, dict):
            raise AppError(422, "invalid_content", "content must be an object")
        updated = dict(patch.content)
    if patch.findings is not None:
        updated["diagnostics"] = patch.findings
    if patch.remedies is not None:
        updated["remedies"] = patch.remedies
    return updated


def patch_report(conn, report_id: str, patch, actor: str) -> dict[str, Any]:
    report = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if not report:
        raise AppError(404, "report_not_found", "Report not found")

    draft = conn.execute(
        """SELECT * FROM report_versions
           WHERE report_id = ? AND state = 'draft' ORDER BY version_no DESC LIMIT 1""",
        (report_id,),
    ).fetchone()

    with conn:
        if draft:
            if patch.expected_version_no != draft["version_no"]:
                raise AppError(
                    409,
                    "stale_version",
                    "expected_version_no does not match the current draft",
                    {"current_version_no": draft["version_no"]},
                )
            content = _apply_patch(json_loads(draft["content_json"], {}) or {}, patch)
            _audit_content(conn, report_id, content)
            digest = content_hash(content)
            cursor = conn.execute(
                """UPDATE report_versions
                   SET content_json = ?, content_hash = ?
                   WHERE version_id = ? AND state = 'draft' AND content_hash = ?""",
                (canonical_json(content), digest, draft["version_id"], draft["content_hash"]),
            )
            if cursor.rowcount != 1:
                raise AppError(409, "stale_version", "Draft changed while it was being edited")
            record_audit(
                conn,
                actor,
                "report.draft_edited",
                "report_version",
                draft["version_id"],
                {"report_id": report_id, "version_no": draft["version_no"]},
            )
            updated = conn.execute(
                "SELECT * FROM report_versions WHERE version_id = ?", (draft["version_id"],)
            ).fetchone()
            return _version_detail(updated)

        latest = conn.execute(
            """SELECT * FROM report_versions WHERE report_id = ?
               ORDER BY version_no DESC LIMIT 1""",
            (report_id,),
        ).fetchone()
        if not latest:
            raise AppError(404, "version_not_found", "Report has no versions")
        if patch.expected_version_no != latest["version_no"]:
            raise AppError(
                409,
                "stale_version",
                "expected_version_no does not match the latest version",
                {"latest_version_no": latest["version_no"]},
            )
        content = _apply_patch(json_loads(latest["content_json"], {}) or {}, patch)
        _audit_content(conn, report_id, content)
        digest = content_hash(content)
        version_id = new_id("ver")
        version_no = latest["version_no"] + 1
        now = iso()
        try:
            conn.execute(
                """INSERT INTO report_versions
                   (version_id, report_id, version_no, parent_version_id, state, content_json,
                    content_hash, analysis_config_hash, created_by, created_at)
                   VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)""",
                (
                    version_id,
                    report_id,
                    version_no,
                    latest["version_id"],
                    canonical_json(content),
                    digest,
                    latest["analysis_config_hash"],
                    actor,
                    now,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise AppError(409, "stale_version", "A new draft was created concurrently") from exc
        conn.execute(
            "UPDATE reports SET current_draft_version = ? WHERE report_id = ?",
            (version_no, report_id),
        )
        record_audit(
            conn,
            actor,
            "report.draft_created",
            "report_version",
            version_id,
            {"report_id": report_id, "version_no": version_no, "parent_version_id": latest["version_id"]},
        )
        created = conn.execute(
            "SELECT * FROM report_versions WHERE version_id = ?", (version_id,)
        ).fetchone()
        return _version_detail(created)


def submit_report(conn, report_id: str, actor: str, review_ttl_seconds: int) -> dict[str, Any]:
    report = conn.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if not report:
        raise AppError(404, "report_not_found", "Report not found")
    draft = conn.execute(
        """SELECT * FROM report_versions
           WHERE report_id = ? AND state = 'draft' ORDER BY version_no DESC LIMIT 1""",
        (report_id,),
    ).fetchone()
    if not draft:
        raise AppError(409, "no_draft", "Report has no active draft to submit")
    _audit_content(conn, report_id, json_loads(draft["content_json"], {}) or {})

    now = utcnow()
    expires_at = iso(now + timedelta(seconds=review_ttl_seconds))
    with conn:
        cursor = conn.execute(
            """UPDATE report_versions
               SET state = 'submitted', submitted_by = ?, submitted_at = ?, expires_at = ?
               WHERE version_id = ? AND state = 'draft' AND content_hash = ?""",
            (actor, iso(now), expires_at, draft["version_id"], draft["content_hash"]),
        )
        if cursor.rowcount != 1:
            raise AppError(409, "submit_conflict", "Draft was already submitted or changed")
        conn.execute(
            "UPDATE reports SET current_draft_version = NULL WHERE report_id = ?",
            (report_id,),
        )
        record_audit(
            conn,
            actor,
            "report.submitted",
            "report_version",
            draft["version_id"],
            {"report_id": report_id, "version_no": draft["version_no"], "expires_at": expires_at},
        )
    updated = conn.execute(
        "SELECT * FROM report_versions WHERE version_id = ?", (draft["version_id"],)
    ).fetchone()
    return _version_summary(updated)


def decide_report(
    conn,
    report_id: str,
    version_id: str,
    decision: str,
    comment: str | None,
    actor: str,
) -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        raise AppError(422, "invalid_decision", "decision must be approve or reject")
    if decision == "reject" and not (comment and comment.strip()):
        raise AppError(422, "comment_required", "A rejection requires a comment")

    version = conn.execute(
        "SELECT * FROM report_versions WHERE version_id = ? AND report_id = ?",
        (version_id, report_id),
    ).fetchone()
    if not version:
        raise AppError(404, "version_not_found", "Report version not found")
    if version["state"] != "submitted":
        raise AppError(
            409,
            "not_submitted",
            f"Version is {version['state']}, not submitted",
        )
    if is_expired(version["expires_at"]):
        with conn:
            conn.execute(
                "UPDATE report_versions SET state = 'expired' WHERE version_id = ? AND state = 'submitted'",
                (version_id,),
            )
            conn.commit()
        raise AppError(409, "review_expired", "Review expired before a decision was recorded")

    now = iso()
    with conn:
        cursor = conn.execute(
            """UPDATE report_versions
               SET state = ?, decided_by = ?, decided_at = ?, decision = ?, decision_comment = ?
               WHERE version_id = ? AND state = 'submitted'
                 AND (expires_at IS NULL OR expires_at > ?)""",
            ("approved" if decision == "approve" else "rejected",
             actor,
             now,
             decision,
             comment,
             version_id,
             now),
        )
        if cursor.rowcount != 1:
            raise AppError(409, "decision_conflict", "Version was already decided or expired")
        record_audit(
            conn,
            actor,
            f"report.{decision}",
            "report_version",
            version_id,
            {"report_id": report_id, "comment": comment},
        )
    updated = conn.execute(
        "SELECT * FROM report_versions WHERE version_id = ?", (version_id,)
    ).fetchone()
    return _version_summary(updated)


def export_version(conn, report_id: str, version_id: str) -> dict[str, Any]:
    version = conn.execute(
        "SELECT * FROM report_versions WHERE version_id = ? AND report_id = ?",
        (version_id, report_id),
    ).fetchone()
    if not version:
        raise AppError(404, "version_not_found", "Report version not found")
    if version["state"] != "approved":
        raise AppError(
            409,
            "not_approved",
            f"Version is {version['state']}; only approved versions can be exported",
        )
    if is_expired(version["expires_at"]):
        with conn:
            conn.execute(
                "UPDATE report_versions SET state = 'expired' WHERE version_id = ? AND state = 'approved'",
                (version_id,),
            )
            conn.commit()
        raise AppError(409, "approval_expired", "Approval expired; a new review is required")

    analysis = conn.execute(
        """SELECT a.config_hash FROM reports r JOIN analyses a ON a.analysis_id = r.analysis_id
           WHERE r.report_id = ?""",
        (report_id,),
    ).fetchone()
    if not analysis or analysis["config_hash"] != version["analysis_config_hash"]:
        raise AppError(
            409,
            "analysis_config_mismatch",
            "Report version does not match its analysis configuration",
        )

    content = json_loads(version["content_json"], {}) or {}
    if content_hash(content) != version["content_hash"]:
        raise AppError(409, "content_integrity", "Stored report content does not match its frozen hash")
    _audit_content(conn, report_id, content)
    return {
        "report_id": report_id,
        "version": _version_summary(version),
        "exported_at": iso(),
        "content": content,
    }


def export_latest_approved(conn, report_id: str) -> dict[str, Any]:
    report = conn.execute("SELECT report_id FROM reports WHERE report_id = ?", (report_id,)).fetchone()
    if not report:
        raise AppError(404, "report_not_found", "Report not found")
    version = conn.execute(
        """SELECT * FROM report_versions
           WHERE report_id = ? AND state = 'approved'
             AND (expires_at IS NULL OR expires_at > ?)
           ORDER BY version_no DESC LIMIT 1""",
        (report_id, iso()),
    ).fetchone()
    if not version:
        raise AppError(409, "no_approved_version", "Report has no approved version")
    return export_version(conn, report_id, version["version_id"])
