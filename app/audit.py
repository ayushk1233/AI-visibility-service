from __future__ import annotations

from typing import Any

from .util import canonical_json, iso, new_id


def record_audit(
    conn,
    actor: str,
    action: str,
    target_type: str,
    target_id: str,
    detail: Any = None,
) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (event_id, actor, action, target_type, target_id, detail_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id("evt"),
            actor,
            action,
            target_type,
            target_id,
            canonical_json(detail or {}),
            iso(),
        ),
    )
