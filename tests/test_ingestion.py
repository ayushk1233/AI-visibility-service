from __future__ import annotations

import pytest

from app.errors import AppError
from app.ingestion import load_pack
from tests.helpers import auth, default_rows, import_pack, write_pack


def _write(root, responses=None, citations=None):
    write_pack(root, responses=responses, citations=citations)


def test_missing_response_field_is_rejected(pack_dir, test_settings):
    _, responses, _ = default_rows()
    del responses[0]["answer_text"]
    _write(pack_dir, responses=responses)

    with pytest.raises(AppError) as exc:
        load_pack(pack_dir, test_settings)
    assert exc.value.code == "invalid_records"
    assert exc.value.details["error_count"] == 1


def test_bad_engine_is_rejected(pack_dir, test_settings):
    _, responses, _ = default_rows()
    responses[0]["engine"] = "not-an-engine"
    _write(pack_dir, responses=responses)

    with pytest.raises(AppError) as exc:
        load_pack(pack_dir, test_settings)
    assert exc.value.code == "invalid_records"


def test_duplicate_prompt_engine_pair_is_rejected(pack_dir, test_settings):
    _, responses, _ = default_rows()
    duplicate = dict(responses[0])
    duplicate["response_id"] = "resp-duplicate"
    _write(pack_dir, responses=responses + [duplicate])

    with pytest.raises(AppError) as exc:
        load_pack(pack_dir, test_settings)
    assert exc.value.code == "invalid_pack"
    assert "Duplicate prompt-engine pairs" in str(exc.value.details)


def test_unknown_citation_response_is_rejected(pack_dir, test_settings):
    _, _, citations = __import__("tests.helpers", fromlist=["default_rows"]).default_rows()
    citations[0]["response_id"] = "missing-response"
    _write(pack_dir, citations=citations)

    with pytest.raises(AppError) as exc:
        load_pack(pack_dir, test_settings)
    assert exc.value.code == "invalid_pack"
    assert "unknown response" in str(exc.value.details)


def test_invalid_pack_does_not_erase_existing_dataset(client, pack_dir):
    first = import_pack(client)
    assert first.status_code == 201
    dataset_id = first.json()["dataset"]["dataset_id"]
    assert first.json()["counts"] == {"prompts": 2, "responses": 6, "citations": 2}

    _, responses, _ = default_rows()
    del responses[0]["answer_text"]
    _write(pack_dir, responses=responses)
    second = import_pack(client)
    assert second.status_code == 422
    assert second.json()["error"]["code"] == "invalid_records"

    current = client.get(f"/datasets/{dataset_id}", headers=auth())
    assert current.status_code == 200
    assert current.json()["counts"] == {"prompts": 2, "responses": 6}


def test_nested_pack_root_is_supported(tmp_path, test_settings):
    root = tmp_path / "nested"
    write_pack(root, nested=True)
    pack = load_pack(root, test_settings)
    assert len(pack.prompts) == 2
    assert len(pack.responses) == 6
    assert len(pack.citations) == 2
