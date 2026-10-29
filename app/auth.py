from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Depends, Header, Request

from .errors import AppError


@dataclass(frozen=True)
class Identity:
    actor: str
    role: str


def _parse_bearer(authorization: str | None) -> str:
    if not authorization:
        raise AppError(401, "missing_token", "Authorization bearer token is required")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(401, "invalid_token", "Authorization header must use a bearer token")
    return token.strip()


def get_identity(
    request: Request,
    authorization: str | None = Header(default=None),
) -> Identity:
    token = _parse_bearer(authorization)
    settings = request.app.state.settings
    if secrets.compare_digest(token, settings.analyst_token):
        return Identity(actor="analyst", role="analyst")
    if secrets.compare_digest(token, settings.reviewer_token):
        return Identity(actor="reviewer", role="reviewer")
    raise AppError(401, "invalid_token", "Token is not recognised")


def require_analyst(identity: Identity = Depends(get_identity)) -> Identity:
    if identity.role != "analyst":
        raise AppError(403, "forbidden", "Analyst role required")
    return identity


def require_reviewer(identity: Identity = Depends(get_identity)) -> Identity:
    if identity.role != "reviewer":
        raise AppError(403, "forbidden", "Reviewer role required")
    return identity


def require_any(identity: Identity = Depends(get_identity)) -> Identity:
    return identity
