from __future__ import annotations

import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from .analysis import recover_interrupted_analyses
from .api.routes import router
from .config import Settings, get_settings
from .db import Database
from .errors import AppError

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = Database(settings.database_path)
    db.init()
    recover_interrupted_analyses(db)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        app.state.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(
        title="Kasparro Visibility Service",
        version=__version__,
        description=(
            "Classify archived AI answers, score Kasparro visibility, generate evidence-backed "
            "diagnostics, and enforce analyst/reviewer review before export."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.db = db
    app.state.executor = ThreadPoolExecutor(max_workers=1)
    app.include_router(router)

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
        request.state.request_id = request_id
        raw_length = request.headers.get("content-length")
        if raw_length:
            try:
                too_large = int(raw_length) > settings.max_upload_bytes
            except ValueError:
                too_large = False
            if too_large:
                return JSONResponse(
                    status_code=413,
                    content={
                        "error": {
                            "code": "payload_too_large",
                            "message": "Request body exceeds the configured size limit",
                            "details": {"max_bytes": settings.max_upload_bytes},
                            "request_id": request_id,
                        }
                    },
                    headers={"X-Request-ID": request_id},
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(AppError)
    async def app_error_handler(request: Request, exc: AppError):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Request validation failed",
                    "details": jsonable_encoder(exc.errors()),
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception):
        logger.exception("Unhandled request error", exc_info=exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "code": "internal_error",
                    "message": "An unexpected error occurred",
                    "details": None,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.get("/")
    def root() -> dict:
        return {
            "service": "kasparro-visibility",
            "version": __version__,
            "docs": "/docs",
            "health": "/health",
            "mode": settings.llm_mode,
        }

    return app


app = create_app()
