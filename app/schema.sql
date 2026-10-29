PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS datasets (
    dataset_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    run_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    imported_by TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    UNIQUE (run_id, content_hash)
);

CREATE TABLE IF NOT EXISTS prompts (
    prompt_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS responses (
    response_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    prompt_id TEXT NOT NULL REFERENCES prompts(prompt_id) ON DELETE CASCADE,
    run_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    engine_name TEXT,
    model_version TEXT,
    answer_text TEXT NOT NULL,
    answer_markdown TEXT,
    capture_status TEXT NOT NULL,
    provider_timestamp TEXT,
    archive_stored_at TEXT NOT NULL,
    settings_json TEXT NOT NULL,
    capture_route TEXT,
    provider_api_version TEXT,
    provider_task_id TEXT
);

CREATE TABLE IF NOT EXISTS citations (
    citation_id TEXT PRIMARY KEY,
    response_id TEXT NOT NULL REFERENCES responses(response_id) ON DELETE CASCADE,
    prompt_id TEXT NOT NULL,
    engine TEXT NOT NULL,
    "order" INTEGER NOT NULL,
    citation_type TEXT NOT NULL,
    url TEXT NOT NULL,
    domain TEXT,
    title TEXT,
    source_name TEXT,
    publication_date TEXT
);

CREATE TABLE IF NOT EXISTS analyses (
    analysis_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES datasets(dataset_id) ON DELETE CASCADE,
    config_json TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    lease_until TEXT,
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS analysis_results (
    result_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    response_id TEXT NOT NULL REFERENCES responses(response_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    reason_code TEXT,
    reason_detail TEXT,
    labels_json TEXT,
    evidence_json TEXT,
    raw_output_json TEXT,
    attempt_log_json TEXT,
    classifier_version TEXT,
    model_mode TEXT,
    model_name TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_estimate REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (analysis_id, response_id)
);

CREATE TABLE IF NOT EXISTS reports (
    report_id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES analyses(analysis_id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    current_draft_version INTEGER
);

CREATE TABLE IF NOT EXISTS report_versions (
    version_id TEXT PRIMARY KEY,
    report_id TEXT NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    version_no INTEGER NOT NULL,
    parent_version_id TEXT,
    state TEXT NOT NULL,
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    analysis_config_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    submitted_by TEXT,
    submitted_at TEXT,
    decided_by TEXT,
    decided_at TEXT,
    decision TEXT,
    decision_comment TEXT,
    expires_at TEXT,
    UNIQUE (report_id, version_no)
);

CREATE TABLE IF NOT EXISTS audit_log (
    event_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_prompts_dataset ON prompts(dataset_id);
CREATE INDEX IF NOT EXISTS idx_responses_prompt_engine ON responses(prompt_id, engine);
CREATE INDEX IF NOT EXISTS idx_responses_dataset ON responses(dataset_id);
CREATE INDEX IF NOT EXISTS idx_citations_response ON citations(response_id);
CREATE INDEX IF NOT EXISTS idx_analyses_config ON analyses(dataset_id, config_hash, mode);
CREATE INDEX IF NOT EXISTS idx_analysis_results_status ON analysis_results(analysis_id, status);
CREATE INDEX IF NOT EXISTS idx_report_versions_report ON report_versions(report_id, state);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_draft_per_report
    ON report_versions(report_id) WHERE state = 'draft';
