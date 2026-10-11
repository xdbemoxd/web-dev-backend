"""Paginación común para listados (el muro carga en bloques de 20, F3.1).

Dos formas:
- `Page` (page/size con total) para tablas de administración.
- `CursorPage` (keyset por `(fecha, id)`) para listas con scroll infinito: no repite ni salta
  elementos cuando llegan nuevos y no se vuelve más lenta con cada página (D15).
"""

import base64
import binascii
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from math import ceil

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import AppError


class PageParams(BaseModel):
    page: int = 1
    size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.size


def page_params(
    page: int = Query(1, ge=1, description="Número de página, empieza en 1"),
    size: int = Query(20, ge=1, le=50, description="Elementos por página"),
) -> PageParams:
    return PageParams(page=page, size=size)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    size: int
    pages: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> "Page[T]":
        return cls(
            items=items,
            total=total,
            page=params.page,
            size=params.size,
            pages=ceil(total / params.size) if total else 0,
        )


# ---------------------------------------------------------------- cursor (keyset)
@dataclass(frozen=True)
class Cursor:
    fecha: datetime
    id: int


def encode_cursor(fecha: datetime, id_: int) -> str:
    raw = f"{fecha.isoformat()}|{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(value: str) -> Cursor:
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()
        fecha, id_ = raw.split("|")
        cursor = Cursor(datetime.fromisoformat(fecha), int(id_))
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise CursorInvalido() from exc
    if cursor.fecha.tzinfo is None:
        raise CursorInvalido()
    return cursor


class CursorInvalido(AppError):
    status_code, code, title = 422, "invalid_cursor", "El cursor de paginación no es válido"

    def __init__(self) -> None:
        super().__init__(
            errors=[{"field": "cursor", "message": "Cursor inválido", "type": "value_error"}]
        )


@dataclass(frozen=True)
class CursorParams:
    cursor: Cursor | None
    limite: int


def cursor_params(
    cursor: str | None = Query(None, description="`siguiente` de la página anterior"),
    limite: int = Query(20, ge=1, le=50, description="Elementos por página"),
) -> CursorParams:
    return CursorParams(cursor=decode_cursor(cursor) if cursor else None, limite=limite)


class CursorPage[T](BaseModel):
    items: list[T]
    siguiente: str | None = None  # None: no hay más páginas

    @classmethod
    def build(
        cls, filas: list[T], params: CursorParams, clave: Callable[[T], tuple[datetime, int]]
    ) -> "CursorPage[T]":
        """`filas` trae hasta `limite + 1` elementos: si llega el extra, hay otra página."""
        items = filas[: params.limite]
        siguiente = None
        if len(filas) > params.limite:
            siguiente = encode_cursor(*clave(items[-1]))
        return cls(items=items, siguiente=siguiente)
