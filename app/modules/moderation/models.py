"""Modelos SQLAlchemy (tablas) del módulo moderation. Privado: solo lo usa este módulo.

Las tablas las crea la migración 0002 (docs/base-de-datos/schema.sql). `moderacion` es un
historial inmutable: los triggers rechazan UPDATE, DELETE y TRUNCATE (F4.6). Las FK apuntan a
`publicacion` y `usuario` por nombre de tabla: moderation no importa modelos de otros módulos.
"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, SmallInteger, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

TIMESTAMPTZ = DateTime(timezone=True)


class AccionModeracion(Base):
    __tablename__ = "accion_moderacion"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))


class Moderacion(Base):
    __tablename__ = "moderacion"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    # NULL cuando la publicación se eliminó: el historial sobrevive (F4.5, HU-22).
    publicacion_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("publicacion.id", ondelete="SET NULL")
    )
    accion_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("accion_moderacion.id", ondelete="RESTRICT")
    )
    moderador_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    motivo: Mapped[str | None] = mapped_column(String(500))
    titulo_al_momento: Mapped[str] = mapped_column(String(150))
    autor_id_al_momento: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    fecha: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    accion: Mapped[AccionModeracion] = relationship(lazy="joined", innerjoin=True)
