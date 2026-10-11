"""Salud del servicio. /health lo usa Render; /health/ready, el keep-alive y el frontend."""

from typing import Literal

from fastapi import APIRouter, Response
from pydantic import BaseModel

from app.core import cache
from app.core.db import ping_db

router = APIRouter(prefix="/health", tags=["health"])

Check = Literal["ok", "down"]


class Liveness(BaseModel):
    status: Literal["ok"]


class Readiness(BaseModel):
    status: Literal["ok", "degraded", "down"]
    db: Check
    redis: Check


@router.get("", response_model=Liveness, summary="El proceso está vivo")
def liveness() -> Liveness:
    return Liveness(status="ok")


@router.get(
    "/ready",
    response_model=Readiness,
    summary="Estado de las dependencias",
    description=(
        "Comprueba Postgres y Redis. Sin Redis el servicio sigue operativo (degraded, 200); "
        "sin base de datos no puede atender peticiones (down, 503)."
    ),
    responses={503: {"model": Readiness, "description": "La base de datos no responde"}},
)
def readiness(response: Response) -> Readiness:
    db: Check = "ok" if ping_db() else "down"
    redis: Check = "ok" if cache.ping() else "down"
    if db == "down":
        response.status_code = 503
        status = "down"
    else:
        status = "ok" if redis == "ok" else "degraded"
    return Readiness(status=status, db=db, redis=redis)
