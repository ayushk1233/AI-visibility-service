from __future__ import annotations

import httpx
from pydantic import ValidationError

from ..config import Settings
from ..models import ClassificationResult
from .base import AdapterResult, ModelError, extract_json_object
from .prompts import SYSTEM_PROMPT, build_user_prompt


class OpenAICompatibleClassifier:
    mode = "live"

    def __init__(self, settings: Settings):
        self.settings = settings

    def classify(self, question: str, answer: str, citations: list[dict]) -> AdapterResult:
        url = f"{self.settings.llm_base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_prompt(
                        question,
                        answer,
                        citations,
                        self.settings.llm_max_input_chars,
                    ),
                },
            ],
            "temperature": 0,
            "max_tokens": 1000,
        }
        try:
            with httpx.Client(timeout=self.settings.llm_timeout_seconds) as client:
                response = client.post(url, headers=headers, json=payload)
        except httpx.TimeoutException as exc:
            raise ModelError("timeout", f"Model request timed out: {exc}", True) from exc
        except httpx.HTTPError as exc:
            raise ModelError("transport_error", f"Model request failed: {exc}", True) from exc

        if response.status_code == 429:
            retry_after = 3.0
            header_val = response.headers.get("retry-after")
            if header_val:
                try:
                    retry_after = float(header_val)
                except ValueError:
                    pass
            raise ModelError(
                "provider_error",
                "Model provider returned HTTP 429 (rate limited)",
                True,
                retry_after=retry_after,
            )
        if response.status_code >= 500 or response.status_code == 408:
            raise ModelError(
                "provider_error",
                f"Model provider returned HTTP {response.status_code}",
                True,
            )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise ModelError(
                "provider_rejected",
                f"Model provider returned HTTP {response.status_code}: {detail}",
                False,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise ModelError("malformed_response", "Provider response was not JSON", True) from exc

        choices = data.get("choices") or []
        if not choices:
            raise ModelError("empty_output", "Provider returned no choices", True)
        message = choices[0].get("message") or {}
        raw = message.get("content")
        if not isinstance(raw, str) or not raw.strip():
            raise ModelError("empty_output", "Provider returned empty content", True)

        parsed = extract_json_object(raw)
        try:
            classification = ClassificationResult.model_validate(parsed)
        except ValidationError as exc:
            raise ModelError("invalid_classification", str(exc), True) from exc

        usage = data.get("usage") or {}
        return AdapterResult(
            classification=classification,
            raw_output=raw,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            model_name=data.get("model") or self.settings.llm_model,
        )
