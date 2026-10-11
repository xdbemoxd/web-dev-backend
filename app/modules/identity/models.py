"""Modelos SQLAlchemy (tablas) del módulo identity. Privado: solo lo usa este módulo.

Las tablas las crea la migración 0002 (docs/base-de-datos/schema.sql). Aquí se declaran con los
mismos nombres y tipos para el ORM; CHECK, índices parciales y triggers viven solo en la base.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    LargeBinary,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

TIMESTAMPTZ = DateTime(timezone=True)


class Rol(Base):
    __tablename__ = "rol"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))


class EstadoSolicitud(Base):
    __tablename__ = "estado_solicitud"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))


class TipoEventoCuenta(Base):
    __tablename__ = "tipo_evento_cuenta"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))


class Usuario(Base):
    __tablename__ = "usuario"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    nombre_completo: Mapped[str] = mapped_column(String(120))
    correo: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    rol_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("rol.id", ondelete="RESTRICT"), server_default=text("1")
    )
    activo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    debe_cambiar_password: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    foto_ruta: Mapped[str | None] = mapped_column(String(255))
    creado_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    rol: Mapped[Rol] = relationship(lazy="joined")


class SolicitudProfesor(Base):
    __tablename__ = "solicitud_profesor"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    estado_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("estado_solicitud.id", ondelete="RESTRICT"),
        server_default=text("1"),
    )
    creada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    resuelta_por: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    resuelta_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    motivo_rechazo: Mapped[str | None] = mapped_column(String(500))

    estado: Mapped[EstadoSolicitud] = relationship(lazy="joined")


class EventoCuenta(Base):
    __tablename__ = "evento_cuenta"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    tipo_evento_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("tipo_evento_cuenta.id", ondelete="RESTRICT")
    )
    realizado_por: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT")
    )
    rol_anterior_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("rol.id", ondelete="RESTRICT")
    )
    rol_nuevo_id: Mapped[int | None] = mapped_column(
        SmallInteger, ForeignKey("rol.id", ondelete="RESTRICT")
    )
    solicitud_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("solicitud_profesor.id", ondelete="RESTRICT"), unique=True
    )
    motivo: Mapped[str | None] = mapped_column(String(500))
    fecha: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())

    tipo: Mapped[TipoEventoCuenta] = relationship(lazy="joined")
    rol_anterior: Mapped[Rol | None] = relationship(foreign_keys=[rol_anterior_id], lazy="joined")
    rol_nuevo: Mapped[Rol | None] = relationship(foreign_keys=[rol_nuevo_id], lazy="joined")


class Sesion(Base):
    __tablename__ = "sesion"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, server_default=func.gen_random_uuid()
    )
    usuario_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("usuario.id", ondelete="CASCADE")
    )
    creada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    expira_absoluta_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    revocada_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
    motivo_revocacion: Mapped[str | None] = mapped_column(String(20))


class RefreshToken(Base):
    __tablename__ = "refresh_token"

    token_hash: Mapped[bytes] = mapped_column(LargeBinary, primary_key=True)
    sesion_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sesion.id", ondelete="CASCADE"))
    emitido_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    expira_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ)
    usado_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)
