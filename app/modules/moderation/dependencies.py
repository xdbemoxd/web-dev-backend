"""Dependencias de FastAPI propias del módulo moderation."""

from datetime import date

from fastapi import Query
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.modules.identity.dependencies import require_role
from app.modules.identity.schemas import RolId
from app.modules.moderation.schemas import FiltrosHistorial
from app.modules.posts.schemas import AccionModeracionId, FiltroModeracion, FiltrosBandeja

# F1.5: solo administradores, comprobado en el servidor en cada endpoint.
solo_admin = require_role(RolId.ADMINISTRADOR)


def filtros_bandeja(
    estado: FiltroModeracion = Query(
        FiltroModeracion.SIN_REVISAR, description="Pestaña de la bandeja"
    ),
    categoria_id: int | None = Query(None),
    autor_id: int | None = Query(None),
    desde: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
    hasta: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
) -> FiltrosBandeja:
    try:
        return FiltrosBandeja(
            estado=estado, categoria_id=categoria_id, autor_id=autor_id, desde=desde, hasta=hasta
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


def filtros_historial(
    moderador_id: int | None = Query(None, description="Id de `GET /moderacion/moderadores`"),
    accion: AccionModeracionId | None = Query(
        None, description="1 revisar, 2 ocultar, 3 restaurar, 4 eliminar"
    ),
    desde: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
    hasta: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
) -> FiltrosHistorial:
    try:
        return FiltrosHistorial(moderador_id=moderador_id, accion=accion, desde=desde, hasta=hasta)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
