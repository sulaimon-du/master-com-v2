import logging
from typing import Literal

from fastapi import APIRouter, Response, status
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.core.db import DbSession, ping

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)


class Health(BaseModel):
    status: Literal["ok", "unavailable"]


class Readiness(Health):
    database: Literal["ok", "unavailable"]


@router.get("/health", response_model=Health)
def liveness() -> Health:
    """Process is up. Never touches the database, so a slow DB does not restart the container."""
    return Health(status="ok")


@router.get(
    "/health/ready",
    response_model=Readiness,
    responses={503: {"model": Readiness}},
)
def readiness(response: Response, db: DbSession) -> Readiness:
    """Process can serve traffic: the database answers."""
    try:
        ok = ping(db)
    except SQLAlchemyError:
        log.exception("readiness check: database unavailable")
        ok = False
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Readiness(status="unavailable", database="unavailable")
    return Readiness(status="ok", database="ok")
