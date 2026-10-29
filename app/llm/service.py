from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings
from ..models import ClassificationResult
from ..util import quote_in_text
from .base import ClassifierAdapter, ModelError
from .openai_compat import OpenAICompatibleClassifier
from .replay import ReplayClassifier


@dataclass
class ClassificationOutcome:
    status: str
    classification: ClassificationResult | None = None
    reason_code: str | None = None
    reason_detail: str | None = None
    raw_output: str | None = None
    attempt_log: list[dict[str, Any]] = field(default_factory=list)
    attempts: int = 0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float | None = None
    model_name: str = ""
    model_mode: str = ""


def _estimate_cost(settings: Settings, input_tokens: int, output_tokens: int) -> float | None:
    if settings.llm_input_cost_per_1k == 0 and settings.llm_output_cost_per_1k == 0:
        return 0.0
    return round(
        (input_tokens / 1000.0) * settings.llm_input_cost_per_1k
        + (output_tokens / 1000.0) * settings.llm_output_cost_per_1k,
        8,
    )


class ClassifierService:
    def __init__(self, settings: Settings, adapter: ClassifierAdapter | None = None):
        self.settings = settings
        if adapter is not None:
            self.adapter = adapter
        elif settings.llm_mode == "replay":
            self.adapter = ReplayClassifier()
        elif settings.llm_mode == "live":
            self.adapter = OpenAICompatibleClassifier(settings)
        else:
            raise ValueError(f"Unsupported LLM_MODE: {settings.llm_mode}")

    def classify(self, question: str, answer: str, citations: list[dict]) -> ClassificationOutcome:
        if not answer or not answer.strip():
            return ClassificationOutcome(
                status="skipped",
                reason_code="empty_answer",
                reason_detail="Response has no usable answer text",
                model_mode=self.adapter.mode,
            )

        start = time.perf_counter()
        attempt_log: list[dict[str, Any]] = []
        total_input = 0
        total_output = 0
        last_raw: str | None = None
        last_error: ModelError | None = None
        model_name = getattr(self.settings, "llm_model", "")

        for attempt in range(1, self.settings.llm_max_attempts + 1):
            attempt_start = time.perf_counter()
            try:
                result = self.adapter.classify(question, answer, citations)
                last_raw = result.raw_output
                model_name = result.model_name or model_name
                total_input += result.input_tokens or 0
                total_output += result.output_tokens or 0

                if len(result.raw_output) > self.settings.llm_max_output_chars:
                    raise ModelError(
                        "output_too_large",
                        f"Model output exceeded {self.settings.llm_max_output_chars} characters",
                        False,
                    )

                self._validate_evidence(result.classification, answer)
                latency_ms = int((time.perf_counter() - start) * 1000)
                attempt_log.append(
                    {
                        "attempt": attempt,
                        "outcome": "succeeded",
                        "latency_ms": int((time.perf_counter() - attempt_start) * 1000),
                    }
                )
                return ClassificationOutcome(
                    status="succeeded",
                    classification=result.classification,
                    raw_output=result.raw_output,
                    attempt_log=attempt_log,
                    attempts=attempt,
                    latency_ms=latency_ms,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cost_estimate=_estimate_cost(self.settings, total_input, total_output),
                    model_name=model_name,
                    model_mode=self.adapter.mode,
                )
            except ModelError as exc:
                last_error = exc
                attempt_log.append(
                    {
                        "attempt": attempt,
                        "outcome": "failed",
                        "error_code": exc.code,
                        "detail": exc.detail[:500],
                        "retryable": exc.retryable,
                        "latency_ms": int((time.perf_counter() - attempt_start) * 1000),
                    }
                )
                if not exc.retryable or attempt >= self.settings.llm_max_attempts:
                    break
                sleep_seconds = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else (self.settings.llm_retry_backoff_seconds * attempt)
                )
                time.sleep(sleep_seconds)
            except Exception as exc:  # pragma: no cover - defensive boundary
                last_error = ModelError("internal_error", str(exc), False)
                attempt_log.append(
                    {
                        "attempt": attempt,
                        "outcome": "failed",
                        "error_code": "internal_error",
                        "detail": str(exc)[:500],
                        "retryable": False,
                        "latency_ms": int((time.perf_counter() - attempt_start) * 1000),
                    }
                )
                break

        latency_ms = int((time.perf_counter() - start) * 1000)
        return ClassificationOutcome(
            status="failed",
            reason_code=last_error.code if last_error else "unknown_error",
            reason_detail=last_error.detail if last_error else "Unknown model failure",
            raw_output=last_raw,
            attempt_log=attempt_log,
            attempts=len(attempt_log),
            latency_ms=latency_ms,
            input_tokens=total_input,
            output_tokens=total_output,
            cost_estimate=_estimate_cost(self.settings, total_input, total_output),
            model_name=model_name,
            model_mode=self.adapter.mode,
        )

    @staticmethod
    def _validate_evidence(classification: ClassificationResult, answer: str) -> None:
        if classification.kasparro_present and not classification.evidence:
            raise ModelError(
                "invalid_evidence",
                "Present classification has no evidence quote",
                True,
            )
        for evidence in classification.evidence:
            if not quote_in_text(evidence.quote, answer):
                raise ModelError(
                    "invalid_evidence",
                    "Evidence quote does not occur in the answer text",
                    True,
                )
