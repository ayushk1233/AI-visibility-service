from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Settings
from .errors import AppError
from .models import CitationRecord, PromptRecord, ResponseRecord
from .util import canonical_json, content_hash, iso, new_id


@dataclass
class PackData:
    source_dir: Path
    run: dict[str, Any]
    prompts: list[PromptRecord]
    responses: list[ResponseRecord]
    citations: list[CitationRecord]
    content_hash: str


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AppError(422, "missing_file", f"Missing required file: {path.name}") from exc
    except ValueError as exc:
        raise AppError(422, "malformed_json", f"Invalid JSON in {path.name}: {exc}") from exc


def _load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    if not path.is_file():
        raise AppError(422, "missing_file", f"Missing required file: {path.name}")
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError as exc:
            raise AppError(
                422,
                "malformed_jsonl",
                f"Invalid JSON in {path.name} at line {line_number}: {exc}",
            ) from exc
        if not isinstance(value, dict):
            raise AppError(
                422,
                "malformed_jsonl",
                f"{path.name} line {line_number} is not a JSON object",
            )
        rows.append((line_number, value))
    return rows


def _validate_records(
    name: str,
    rows: list[tuple[int, dict[str, Any]]],
    model: type[PromptRecord] | type[ResponseRecord] | type[CitationRecord],
) -> list[Any]:
    values = []
    errors = []
    for line_number, row in rows:
        try:
            values.append(model.model_validate(row))
        except ValidationError as exc:
            errors.append({"file": name, "line": line_number, "error": exc.errors()})
    if errors:
        raise AppError(
            422,
            "invalid_records",
            f"{name} contains invalid records",
            {"errors": errors[:20], "error_count": len(errors)},
        )
    return values


def _verify_manifest(source_dir: Path, manifest: dict[str, Any], counts: dict[str, int]) -> None:
    declared_counts = manifest.get("counts") or {}
    for key, expected in declared_counts.items():
        if key in counts and counts[key] != expected:
            raise AppError(
                422,
                "count_mismatch",
                f"Manifest expects {expected} {key}, found {counts[key]}",
            )
    ignored_missing = {"Assignment.docx", "Assignment.pdf"}
    for relative, expected in (manifest.get("sha256") or {}).items():
        path = source_dir / relative
        if not path.is_file():
            if relative in ignored_missing:
                continue
            raise AppError(422, "missing_file", f"Missing manifest file: {relative}")
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise AppError(422, "checksum_mismatch", f"Checksum mismatch: {relative}")


def load_pack(source_dir: Path, settings: Settings) -> PackData:
    root = settings.data_dir.resolve()
    candidate = source_dir if source_dir.is_absolute() else root / source_dir
    resolved = candidate.resolve()
    if resolved != root and not resolved.is_relative_to(root):
        raise AppError(403, "path_not_allowed", "source_dir must be inside DATA_DIR")

    if (resolved / "run.json").is_file():
        data_dir = resolved
        pack_root = resolved
    elif (resolved / "data" / "run.json").is_file():
        data_dir = resolved / "data"
        pack_root = resolved
    else:
        raise AppError(422, "missing_file", "Missing required file: run.json")

    run = _load_json(data_dir / "run.json")
    prompt_rows = _load_jsonl(data_dir / "prompts.jsonl")
    response_rows = _load_jsonl(data_dir / "responses.jsonl")
    citation_rows = _load_jsonl(data_dir / "citations.jsonl")

    prompts = _validate_records("prompts.jsonl", prompt_rows, PromptRecord)
    responses = _validate_records("responses.jsonl", response_rows, ResponseRecord)
    citations = _validate_records("citations.jsonl", citation_rows, CitationRecord)

    errors: list[str] = []
    if len({p.prompt_id for p in prompts}) != len(prompts):
        errors.append("prompts.jsonl contains duplicate prompt IDs")
    if len({r.response_id for r in responses}) != len(responses):
        errors.append("responses.jsonl contains duplicate response IDs")
    if len({c.citation_id for c in citations}) != len(citations):
        errors.append("citations.jsonl contains duplicate citation IDs")

    prompt_ids = {p.prompt_id for p in prompts}
    response_ids = {r.response_id for r in responses}
    by_response = {r.response_id: r for r in responses}

    for prompt in prompts:
        if prompt.run_id != run.get("run_id"):
            errors.append(f"Prompt {prompt.prompt_id} has an unexpected run_id")
    for response in responses:
        if response.run_id != run.get("run_id"):
            errors.append(f"Response {response.response_id} has an unexpected run_id")
        if response.prompt_id not in prompt_ids:
            errors.append(f"Response {response.response_id} references an unknown prompt")
        if response.capture_status != "captured":
            errors.append(f"Response {response.response_id} is not marked captured")
    for citation in citations:
        if citation.response_id not in response_ids:
            errors.append(f"Citation {citation.citation_id} references an unknown response")
            continue
        response = by_response[citation.response_id]
        if citation.prompt_id != response.prompt_id:
            errors.append(f"Citation {citation.citation_id} prompt_id does not match its response")
        if citation.engine != response.engine:
            errors.append(f"Citation {citation.citation_id} engine does not match its response")

    pairs = Counter((r.prompt_id, r.engine) for r in responses)
    duplicates = [pair for pair, count in pairs.items() if count > 1]
    if duplicates:
        errors.append(f"Duplicate prompt-engine pairs: {duplicates[:5]}")
    if len(responses) != len(prompts) * 3:
        errors.append(
            f"Expected three responses per prompt, found {len(responses)} responses for {len(prompts)} prompts"
        )

    manifest_path = pack_root / "PACK_MANIFEST.json"
    if manifest_path.is_file():
        manifest = _load_json(manifest_path)
        _verify_manifest(
            pack_root,
            manifest,
            {
                "prompts": len(prompts),
                "responses": len(responses),
                "citations": len(citations),
            },
        )

    if errors:
        raise AppError(
            422,
            "invalid_pack",
            "Input pack failed validation",
            {"errors": errors[:20], "error_count": len(errors)},
        )

    digest = content_hash(
        {
            "run": run,
            "prompts": [p.model_dump(mode="json") for p in prompts],
            "responses": [r.model_dump(mode="json") for r in responses],
            "citations": [c.model_dump(mode="json") for c in citations],
        }
    )
    return PackData(
        source_dir=pack_root,
        run=run,
        prompts=prompts,
        responses=responses,
        citations=citations,
        content_hash=digest,
    )


def import_dataset(
    conn,
    settings: Settings,
    source_dir: str,
    actor: str,
) -> tuple[dict[str, Any], bool]:
    pack = load_pack(Path(source_dir), settings)
    run_id = str(pack.run.get("run_id"))

    existing = conn.execute(
        "SELECT * FROM datasets WHERE run_id = ? AND content_hash = ?",
        (run_id, pack.content_hash),
    ).fetchone()
    if existing:
        return dict(existing), False

    conflict = conn.execute(
        "SELECT dataset_id FROM datasets WHERE run_id = ? LIMIT 1",
        (run_id,),
    ).fetchone()
    if conflict:
        raise AppError(
            409,
            "dataset_conflict",
            "A dataset with this run_id already exists with different content",
            {"dataset_id": conflict["dataset_id"]},
        )

    dataset_id = new_id("ds")
    imported_at = iso()
    with conn:
        conn.execute(
            """INSERT INTO datasets
               (dataset_id, run_id, source_path, run_json, content_hash, imported_by, imported_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                dataset_id,
                run_id,
                str(pack.source_dir),
                canonical_json(pack.run),
                pack.content_hash,
                actor,
                imported_at,
            ),
        )
        conn.executemany(
            "INSERT INTO prompts (prompt_id, dataset_id, run_id, text) VALUES (?, ?, ?, ?)",
            [(p.prompt_id, dataset_id, p.run_id, p.text) for p in pack.prompts],
        )
        conn.executemany(
            """INSERT INTO responses
               (response_id, dataset_id, prompt_id, run_id, engine, engine_name, model_version,
                answer_text, answer_markdown, capture_status, provider_timestamp, archive_stored_at,
                settings_json, capture_route, provider_api_version, provider_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    r.response_id,
                    dataset_id,
                    r.prompt_id,
                    r.run_id,
                    r.engine,
                    r.engine_name,
                    r.model_version,
                    r.answer_text,
                    r.answer_markdown,
                    r.capture_status,
                    r.provider_timestamp,
                    r.archive_stored_at,
                    canonical_json(r.settings or {}),
                    r.capture_route,
                    r.provider_api_version,
                    r.provider_task_id,
                )
                for r in pack.responses
            ],
        )
        conn.executemany(
            """INSERT INTO citations
               (citation_id, response_id, prompt_id, engine, "order", citation_type, url,
                domain, title, source_name, publication_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    c.citation_id,
                    c.response_id,
                    c.prompt_id,
                    c.engine,
                    c.order,
                    c.citation_type,
                    c.url,
                    c.domain,
                    c.title,
                    c.source_name,
                    c.publication_date,
                )
                for c in pack.citations
            ],
        )

    row = conn.execute("SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)).fetchone()
    return dict(row), True
