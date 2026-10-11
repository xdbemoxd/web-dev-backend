"""Esquemas Pydantic (entrada/salida de la API) del módulo moderation."""

from datetime import date, datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, ValidationInfo, field_validator

from app.modules.identity.schemas import AutorOut
from app.modules.posts.schemas import AccionModeracionId, CatalogoOut, PublicacionOut

MOTIVO_MAX = 500
MAX_LOTE = 50

Marca = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Motivo = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MOTIVO_MAX)
]


# ---------------------------------------------------------------- entrada
class AccionIn(BaseModel):
    marca: Marca = Field(description="`marca` de la publicación tal como la viste")


class MotivoIn(AccionIn):
    motivo: Motivo = Field(description="Obligatorio (F4.3, F4.5). El autor lo ve al ocultar")


class ItemLoteIn(BaseModel):
    id: int = Field(gt=0)
    marca: Marca


class LoteIn(BaseModel):
    items: list[ItemLoteIn] = Field(min_length=1, max_length=MAX_LOTE)


class FiltrosHistorial(BaseModel):
    moderador_id: int | None = Field(None, gt=0)
    accion: AccionModeracionId | None = None
    desde: date | None = Field(None, description="Fecha local de Caracas, inclusive")
    hasta: date | None = Field(None, description="Fecha local de Caracas, inclusive")

    @field_validator("hasta")
    @classmethod
    def _rango(cls, value: date | None, info: ValidationInfo) -> date | None:
        desde = info.data.get("desde")
        if value and desde and desde > value:
            raise ValueError("La fecha final no puede ser anterior a la inicial")
        return value


# ---------------------------------------------------------------- salida
class RegistroOut(BaseModel):
    """Una acción del historial (F4.6). Título y autor son los del momento de la acción."""

    id: int
    accion: CatalogoOut
    motivo: str | None
    moderador: AutorOut
    publicacion_id: int | None = Field(description="null: la publicación fue eliminada")
    titulo: str
    autor: AutorOut
    fecha: datetime


class DetalleModeracionOut(BaseModel):
    publicacion: PublicacionOut
    revisada: bool
    marca: str
    historial: list[RegistroOut] = Field(description="De la acción más reciente a la más antigua")


class OmitidaOut(BaseModel):
    id: int
    code: str = Field(description="post_not_found, moderation_conflict o moderation_not_allowed")


class LoteOut(BaseModel):
    revisadas: list[int]
    omitidas: list[OmitidaOut]


class PendientesOut(BaseModel):
    total: int = Field(description="Publicaciones sin revisar (con tope de 1000)")
    ocultas: int = Field(description="Publicaciones ocultas por moderación (con tope de 1000)")
