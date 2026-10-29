from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def is_expired(expires_at: str | None, now: datetime | None = None) -> bool:
    parsed = parse_iso(expires_at)
    if parsed is None:
        return False
    return parsed <= (now or utcnow())


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def content_hash(value: Any) -> str:
    return sha256_text(canonical_json(value))


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def normalize_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def quote_in_text(quote: str, text: str) -> bool:
    return bool(quote) and quote in text


def json_loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
