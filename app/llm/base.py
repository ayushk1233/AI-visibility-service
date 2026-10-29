from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Protocol

from ..models import ClassificationResult


class ModelError(Exception):
    def __init__(
        self,
        code: str,
        detail: str,
        retryable: bool,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class AdapterResult:
    classification: ClassificationResult
    raw_output: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    model_name: str = ""


class ClassifierAdapter(Protocol):
    mode: str

    def classify(
        self,
        question: str,
        answer: str,
        citations: list[dict],
    ) -> AdapterResult: ...


def extract_json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ModelError("malformed_output", "Model output did not contain a JSON object", True)
    try:
        value = json.loads(text[start : end + 1])
    except ValueError as exc:
        raise ModelError("malformed_output", f"Model output was not valid JSON: {exc}", True) from exc
    if not isinstance(value, dict):
        raise ModelError("malformed_output", "Model output JSON was not an object", True)
    return value
