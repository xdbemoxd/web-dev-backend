"""Acceso a datos del módulo moderation: consultas SQLAlchemy. Privado.

No hace commit: la transacción la controla el servicio. Solo inserta y lee: el historial es
inmutable (F4.6).
"""

from datetime import timedelta

from sqlalchemy import ColumnElement, Date, DateTime, cast, func, select, tuple_
from sqlalchemy.orm import Session

from app.core.pagination import Cursor
from app.modules.moderation.models import Moderacion
from app.modules.moderation.schemas import FiltrosHistorial

ZONA = "America/Caracas"  # las fechas de los filtros son locales, igual que en el muro


def _inicio_del_dia(dia) -> ColumnElement:
    return func.timezone(ZONA, cast(cast(dia, Date), DateTime))


def insertar(db: Session, registro: Moderacion) -> Moderacion:
    db.add(registro)
    db.flush()
    return registro


def historial_de(db: Session, publicacion_id: int) -> list[Moderacion]:
    """Usa ix_moderacion_publicacion_fecha."""
    return list(
        db.scalars(
            select(Moderacion)
            .where(Moderacion.publicacion_id == publicacion_id)
            .order_by(Moderacion.fecha.desc(), Moderacion.id.desc())
        )
    )


def ultima_accion(db: Session, publicacion_id: int) -> Moderacion | None:
    return db.scalars(
        select(Moderacion)
        .where(Moderacion.publicacion_id == publicacion_id)
        .order_by(Moderacion.fecha.desc(), Moderacion.id.desc())
        .limit(1)
    ).one_or_none()


def historial(
    db: Session, filtros: FiltrosHistorial, cursor: Cursor | None, limite: int
) -> list[Moderacion]:
    """Completo, de lo más reciente a lo más antiguo (F4.6). Trae limite + 1 filas."""
    stmt = select(Moderacion)
    if filtros.moderador_id:
        stmt = stmt.where(Moderacion.moderador_id == filtros.moderador_id)
    if filtros.accion:
        stmt = stmt.where(Moderacion.accion_id == filtros.accion)
    if filtros.desde:
        stmt = stmt.where(Moderacion.fecha >= _inicio_del_dia(filtros.desde))
    if filtros.hasta:
        stmt = stmt.where(Moderacion.fecha < _inicio_del_dia(filtros.hasta + timedelta(days=1)))
    if cursor is not None:
        stmt = stmt.where(tuple_(Moderacion.fecha, Moderacion.id) < (cursor.fecha, cursor.id))
    stmt = stmt.order_by(Moderacion.fecha.desc(), Moderacion.id.desc()).limit(limite + 1)
    return list(db.scalars(stmt))


def moderadores(db: Session) -> list[int]:
    """Quienes aparecen en el historial, aunque hoy ya no sean administradores."""
    return list(db.scalars(select(Moderacion.moderador_id).distinct()))
