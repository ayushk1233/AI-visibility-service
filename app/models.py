from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PromptRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt_id: str
    run_id: str
    text: str = Field(min_length=1)


class ResponseRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    response_id: str
    run_id: str
    prompt_id: str
    engine: Literal["aimode", "chatgpt", "gemini"]
    engine_name: str | None = None
    model_version: str | None = None
    answer_text: str = Field(min_length=1)
    answer_markdown: str | None = None
    capture_status: str
    provider_timestamp: str | None = None
    archive_stored_at: str
    settings: dict[str, Any] | None = None
    capture_route: str | None = None
    provider_api_version: str | None = None
    provider_task_id: str | None = None


class CitationRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    citation_id: str
    response_id: str
    prompt_id: str
    engine: Literal["aimode", "chatgpt", "gemini"]
    order: int = Field(ge=1)
    citation_type: Literal["inline_hyperlink", "sources_panel"]
    url: str = Field(min_length=1)
    domain: str | None = None
    title: str | None = None
    source_name: str | None = None
    publication_date: str | None = None


class DatasetImportRequest(BaseModel):
    source_dir: str = "."


class AnalysisCreateRequest(BaseModel):
    dataset_id: str
    force: bool = False


class ReportCreateRequest(BaseModel):
    analysis_id: str
    title: str = "Kasparro AI visibility report"


class ReportPatchRequest(BaseModel):
    expected_version_no: int = Field(ge=1)
    content: dict[str, Any] | None = None
    findings: list[dict[str, Any]] | None = None
    remedies: list[dict[str, Any]] | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = None


class Evidence(BaseModel):
    model_config = ConfigDict(extra="ignore")

    quote: str = Field(min_length=1, max_length=4000)
    supports: list[str] = Field(default_factory=list)


class ClassificationResult(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kasparro_present: bool
    recommended: bool = False
    negatively_characterised: bool = False
    competitors: list[str] = Field(default_factory=list)
    recommendation_position: int | None = Field(default=None, ge=1)
    evidence: list[Evidence] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_overlap(self) -> "ClassificationResult":
        if self.recommended and not self.kasparro_present:
            raise ValueError("recommended requires kasparro_present")
        self.competitors = sorted({name.strip() for name in self.competitors if name.strip()})
        return self
