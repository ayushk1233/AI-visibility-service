from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app
from tests.helpers import write_pack


@pytest.fixture
def test_settings(tmp_path):
    return replace(
        get_settings(),
        app_env="test",
        database_path=tmp_path / "test.db",
        data_dir=tmp_path,
        analyst_token="analyst-token",
        reviewer_token="reviewer-token",
        review_ttl_seconds=3600,
        llm_mode="replay",
        llm_model="replay-v1",
        llm_max_attempts=3,
        llm_retry_backoff_seconds=0.0,
        sync_analysis=True,
    )


@pytest.fixture
def app(test_settings):
    return create_app(test_settings)


@pytest.fixture
def client(app, pack_dir):
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def pack_dir(tmp_path):
    root = tmp_path / "pack"
    write_pack(root)
    return root
