from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _resolve_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (BASE_DIR / path).resolve()
    return path


@dataclass(frozen=True)
class Settings:
    app_env: str
    database_path: Path
    data_dir: Path
    analyst_token: str
    reviewer_token: str
    review_ttl_seconds: int
    llm_mode: str
    llm_base_url: str
    llm_api_key: str
    llm_model: str
    llm_timeout_seconds: float
    llm_max_attempts: int
    llm_max_input_chars: int
    llm_max_output_chars: int
    llm_retry_backoff_seconds: float
    llm_input_cost_per_1k: float
    llm_output_cost_per_1k: float
    sync_analysis: bool
    max_upload_bytes: int

    @classmethod
    def from_env(cls) -> "Settings":
        app_env = os.getenv("APP_ENV", "local").strip().lower()
        analyst_token = os.getenv("ANALYST_TOKEN", "dev-analyst-token").strip()
        reviewer_token = os.getenv("REVIEWER_TOKEN", "dev-reviewer-token").strip()
        mode = os.getenv("LLM_MODE", "replay").strip().lower()
        if mode not in {"replay", "live"}:
            raise ValueError("LLM_MODE must be 'replay' or 'live'")
        if app_env not in {"test", "local"} and (
            "dev-analyst-token" in {analyst_token, reviewer_token}
        ):
            logger.warning("Default development tokens are in use; set ANALYST_TOKEN and REVIEWER_TOKEN.")
        return cls(
            app_env=app_env,
            database_path=_resolve_path(os.getenv("DATABASE_PATH", "db/kasparro.db")),
            data_dir=_resolve_path(os.getenv("DATA_DIR", ".")),
            analyst_token=analyst_token,
            reviewer_token=reviewer_token,
            review_ttl_seconds=_env_int("REVIEW_TTL_SECONDS", 86400),
            llm_mode=mode,
            llm_base_url=os.getenv("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/"),
            llm_api_key=os.getenv("LLM_API_KEY", "ollama"),
            llm_model=(
                "replay-v1"
                if mode == "replay"
                else (os.getenv("LLM_MODEL", "").strip() or "llama3.2")
            ),
            llm_timeout_seconds=_env_float("LLM_TIMEOUT_SECONDS", 30.0),
            llm_max_attempts=_env_int("LLM_MAX_ATTEMPTS", 3),
            llm_max_input_chars=_env_int("LLM_MAX_INPUT_CHARS", 30000),
            llm_max_output_chars=_env_int("LLM_MAX_OUTPUT_CHARS", 4000),
            llm_retry_backoff_seconds=_env_float("LLM_RETRY_BACKOFF_SECONDS", 0.5),
            llm_input_cost_per_1k=_env_float("LLM_INPUT_COST_PER_1K", 0.0),
            llm_output_cost_per_1k=_env_float("LLM_OUTPUT_COST_PER_1K", 0.0),
            sync_analysis=_env_bool("SYNC_ANALYSIS", False),
            max_upload_bytes=_env_int("MAX_UPLOAD_BYTES", 10 * 1024 * 1024),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings.from_env()
