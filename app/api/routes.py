from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response

from ..analysis import (
    create_analysis,
    get_analysis_status,
    resume_analysis,
    run_analysis,
)
from ..auth import Identity, require_analyst, require_any, require_reviewer
from ..config import Settings
from ..errors import AppError
from ..ingestion import import_dataset
from ..models import (
    AnalysisCreateRequest,
    DatasetImportRequest,
    DecisionRequest,
    ReportCreateRequest,
    ReportPatchRequest,
)
from ..reports import (
    create_report,
    decide_report,
    export_latest_approved,
    export_version,
    get_report,
    get_version,
    list_versions,
    patch_report,
    submit_report,
)
from ..util import json_loads

router = APIRouter()


def get_conn(request: Request):
    conn = request.app.state.db.connect()
    try:
        yield conn
    finally:
        conn.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def _start_analysis(request: Request, analysis_id: str) -> None:
    settings = request.app.state.settings
    db = request.app.state.db
    if settings.sync_analysis:
        run_analysis(db, settings, analysis_id)
    else:
        request.app.state.executor.submit(run_analysis, db, settings, analysis_id)


@router.get("/health")
def health(request: Request) -> dict:
    try:
        conn = request.app.state.db.connect()
        try:
            conn.execute("SELECT 1")
        finally:
            conn.close()
    except Exception as exc:
        raise AppError(503, "database_unavailable", "Database check failed") from exc
    settings = request.app.state.settings
    return {
        "status": "ok",
        "database": "ok",
        "mode": settings.llm_mode,
        "model": settings.llm_model,
        "service": "kasparro-visibility",
    }


@router.post("/datasets")
def import_dataset_endpoint(
    payload: DatasetImportRequest,
    request: Request,
    response: Response,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    dataset, created = import_dataset(
        conn,
        request.app.state.settings,
        payload.source_dir,
        identity.actor,
    )
    response.status_code = 201 if created else 200
    counts = {
        "prompts": conn.execute(
            "SELECT COUNT(*) AS count FROM prompts WHERE dataset_id = ?", (dataset["dataset_id"],)
        ).fetchone()["count"],
        "responses": conn.execute(
            "SELECT COUNT(*) AS count FROM responses WHERE dataset_id = ?", (dataset["dataset_id"],)
        ).fetchone()["count"],
        "citations": conn.execute(
            """SELECT COUNT(*) AS count FROM citations
               WHERE response_id IN (SELECT response_id FROM responses WHERE dataset_id = ?)""",
            (dataset["dataset_id"],),
        ).fetchone()["count"],
    }
    return {"dataset": dataset, "counts": counts, "created": created}


@router.get("/datasets/{dataset_id}")
def get_dataset(
    dataset_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    dataset = conn.execute(
        "SELECT * FROM datasets WHERE dataset_id = ?", (dataset_id,)
    ).fetchone()
    if not dataset:
        raise AppError(404, "dataset_not_found", "Dataset not found")
    counts = {
        "prompts": conn.execute(
            "SELECT COUNT(*) AS count FROM prompts WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()["count"],
        "responses": conn.execute(
            "SELECT COUNT(*) AS count FROM responses WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()["count"],
    }
    data = dict(dataset)
    data["run"] = json_loads(dataset["run_json"], {})
    data["counts"] = counts
    return data


@router.post("/analyses", status_code=202)
def create_analysis_endpoint(
    payload: AnalysisCreateRequest,
    request: Request,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    analysis = create_analysis(
        conn,
        request.app.state.settings,
        payload.dataset_id,
        identity.actor,
        payload.force,
    )
    _start_analysis(request, analysis["analysis_id"])
    return get_analysis_status(conn, analysis["analysis_id"])


@router.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return get_analysis_status(conn, analysis_id)


@router.post("/analyses/{analysis_id}/resume", status_code=202)
def resume_analysis_endpoint(
    analysis_id: str,
    request: Request,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    resume_analysis(conn, analysis_id, identity.actor)
    _start_analysis(request, analysis_id)
    return get_analysis_status(conn, analysis_id)


@router.post("/reports", status_code=201)
def create_report_endpoint(
    payload: ReportCreateRequest,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    return create_report(conn, payload.analysis_id, payload.title, identity.actor)


@router.get("/reports/{report_id}")
def get_report_endpoint(
    report_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return get_report(conn, report_id)


@router.patch("/reports/{report_id}")
def patch_report_endpoint(
    report_id: str,
    payload: ReportPatchRequest,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    return patch_report(conn, report_id, payload, identity.actor)


@router.get("/reports/{report_id}/versions")
def list_versions_endpoint(
    report_id: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return list_versions(conn, report_id, limit=limit, offset=offset)


@router.get("/reports/{report_id}/versions/{version_id}")
def get_version_endpoint(
    report_id: str,
    version_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return get_version(conn, report_id, version_id)


@router.post("/reports/{report_id}/submit")
def submit_report_endpoint(
    report_id: str,
    request: Request,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_analyst),
) -> dict:
    return submit_report(
        conn,
        report_id,
        identity.actor,
        request.app.state.settings.review_ttl_seconds,
    )


@router.post("/reports/{report_id}/versions/{version_id}/decision")
def decide_report_endpoint(
    report_id: str,
    version_id: str,
    payload: DecisionRequest,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_reviewer),
) -> dict:
    return decide_report(
        conn,
        report_id,
        version_id,
        payload.decision,
        payload.comment,
        identity.actor,
    )


@router.get("/reports/{report_id}/export")
def export_report_endpoint(
    report_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return export_latest_approved(conn, report_id)


@router.get("/reports/{report_id}/versions/{version_id}/export")
def export_version_endpoint(
    report_id: str,
    version_id: str,
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    return export_version(conn, report_id, version_id)


@router.get("/audit")
def audit_endpoint(
    target_type: str | None = None,
    target_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    conn=Depends(get_conn),
    identity: Identity = Depends(require_any),
) -> dict:
    clauses = []
    params: list = []
    if target_type:
        clauses.append("target_type = ?")
        params.append(target_type)
    if target_id:
        clauses.append("target_id = ?")
        params.append(target_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM audit_log {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) AS count FROM audit_log {where}", tuple(params)
    ).fetchone()["count"]
    items = []
    for row in rows:
        item = dict(row)
        item["detail"] = json_loads(row["detail_json"], {})
        items.append(item)
    return {"items": items, "total": total, "limit": limit, "offset": offset}
