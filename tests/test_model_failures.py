from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from app.llm.base import AdapterResult, ModelError, extract_json_object
from app.llm.service import ClassifierService
from app.models import ClassificationResult, Evidence
from app.scoring import score_rows


class RaisingAdapter:
    mode = "test"

    def __init__(self, error: ModelError):
        self.error = error

    def classify(self, question, answer, citations):
        raise self.error


class ReturningAdapter:
    mode = "test"

    def __init__(self, result: AdapterResult):
        self.result = result

    def classify(self, question, answer, citations):
        return self.result


def _settings(test_settings, **overrides):
    return replace(test_settings, llm_retry_backoff_seconds=0.0, **overrides)


def test_retryable_failures_are_bounded_and_explicit(test_settings):
    service = ClassifierService(
        _settings(test_settings, llm_max_attempts=3),
        RaisingAdapter(ModelError("timeout", "timed out", True)),
    )
    outcome = service.classify("question", "Kasparro is mentioned.", [])
    assert outcome.status == "failed"
    assert outcome.reason_code == "timeout"
    assert outcome.attempts == 3
    assert outcome.classification is None
    assert len(outcome.attempt_log) == 3


def test_non_retryable_failure_is_not_retried(test_settings):
    service = ClassifierService(
        _settings(test_settings, llm_max_attempts=3),
        RaisingAdapter(ModelError("provider_rejected", "bad request", False)),
    )
    outcome = service.classify("question", "Kasparro is mentioned.", [])
    assert outcome.status == "failed"
    assert outcome.reason_code == "provider_rejected"
    assert outcome.attempts == 1


def test_malformed_and_empty_outputs_are_failures(test_settings):
    with pytest.raises(ModelError) as malformed:
        extract_json_object("not a JSON object")
    assert malformed.value.code == "malformed_output"

    service = ClassifierService(
        _settings(test_settings, llm_max_attempts=1),
        RaisingAdapter(ModelError("empty_output", "empty", True)),
    )
    outcome = service.classify("question", "Kasparro is mentioned.", [])
    assert outcome.status == "failed"
    assert outcome.reason_code == "empty_output"


def test_oversized_output_is_rejected(test_settings):
    classification = ClassificationResult(
        kasparro_present=True,
        evidence=[Evidence(quote="Kasparro is mentioned.", supports=["kasparro_present"])],
    )
    service = ClassifierService(
        _settings(test_settings, llm_max_output_chars=10),
        ReturningAdapter(AdapterResult(classification=classification, raw_output="x" * 20)),
    )
    outcome = service.classify("question", "Kasparro is mentioned.", [])
    assert outcome.status == "failed"
    assert outcome.reason_code == "output_too_large"
    assert outcome.attempts == 1


def test_invalid_evidence_quote_is_rejected(test_settings):
    classification = ClassificationResult(
        kasparro_present=True,
        evidence=[Evidence(quote="This quote is absent.", supports=["kasparro_present"])],
    )
    service = ClassifierService(
        _settings(test_settings, llm_max_attempts=2),
        ReturningAdapter(AdapterResult(classification=classification, raw_output="{}")),
    )
    outcome = service.classify("question", "Kasparro is mentioned.", [])
    assert outcome.status == "failed"
    assert outcome.reason_code == "invalid_evidence"
    assert outcome.attempts == 2


def test_failed_responses_are_not_scored_as_zero():
    rows = [
        {"engine": "x", "prompt_category": "c", "status": "failed", "labels": {}, "citation_count": 0},
        {"engine": "x", "prompt_category": "c", "status": "skipped", "labels": {}, "citation_count": 0},
    ]
    scores = score_rows(rows, eligible=2)
    assert scores["n"] == 0
    assert scores["failed"] == 1
    assert scores["skipped"] == 1
    assert scores["visibility_index"] is None
    assert scores["inclusion_rate"] is None
    assert scores["coverage"] == 0.0
