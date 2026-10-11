"""Modelos SQLAlchemy (tablas) del módulo posts. Privado: solo lo usa este módulo.

Las tablas las crea la migración 0002 (docs/base-de-datos/schema.sql). Aquí se declaran con los
mismos nombres y tipos para el ORM; CHECK, índices parciales y triggers viven solo en la base.
`autor_id` apunta a `usuario` por nombre de tabla: posts no importa los modelos de identity.
"""

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

TIMESTAMPTZ = DateTime(timezone=True)


class Categoria(Base):
    __tablename__ = "categoria"

    id: Mapped[int] = mapped_column(SmallInteger, Identity(always=True), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(60))
    descripcion: Mapped[str] = mapped_column(String(200), server_default=text("''"))
    color: Mapped[str] = mapped_column(CHAR(7), server_default=text("'#1E40AF'"))
    activa: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    creada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


class Audiencia(Base):
    __tablename__ = "audiencia"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))
    descripcion: Mapped[str] = mapped_column(String(200))
    visible_sin_sesion: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class EstadoPublicacion(Base):
    __tablename__ = "estado_publicacion"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    codigo: Mapped[str] = mapped_column(String(20), unique=True)
    nombre: Mapped[str] = mapped_column(String(50))


class Publicacion(Base):
    __tablename__ = "publicacion"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    autor_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("usuario.id", ondelete="RESTRICT"))
    categoria_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("categoria.id", ondelete="RESTRICT")
    )
    audiencia_id: Mapped[int] = mapped_column(
        SmallInteger, ForeignKey("audiencia.id", ondelete="RESTRICT"), server_default=text("2")
    )
    estado_id: Mapped[int] = mapped_column(
        SmallInteger,
        ForeignKey("estado_publicacion.id", ondelete="RESTRICT"),
        server_default=text("1"),
    )
    titulo: Mapped[str] = mapped_column(String(150))
    contenido: Mapped[str] = mapped_column(Text)
    revisada: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    publicada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())
    editada_en: Mapped[datetime | None] = mapped_column(TIMESTAMPTZ)

    # innerjoin: las tres FK son NOT NULL, y así el SELECT … FOR UPDATE no toca un OUTER JOIN.
    categoria: Mapped[Categoria] = relationship(lazy="joined", innerjoin=True)
    audiencia: Mapped[Audiencia] = relationship(lazy="joined", innerjoin=True)
    estado: Mapped[EstadoPublicacion] = relationship(lazy="joined", innerjoin=True)
    # passive_deletes: el CASCADE lo hace la base, así su trigger encola los archivos (D14).
    imagenes: Mapped[list["ImagenPublicacion"]] = relationship(
        order_by="ImagenPublicacion.orden",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    etiquetas: Mapped[list["PublicacionEtiqueta"]] = relationship(
        order_by="PublicacionEtiqueta.posicion",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class ImagenPublicacion(Base):
    __tablename__ = "imagen_publicacion"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    publicacion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("publicacion.id", ondelete="CASCADE")
    )
    ruta_almacenamiento: Mapped[str] = mapped_column(String(255), unique=True)
    tipo_mime: Mapped[str] = mapped_column(String(20))
    tamano_bytes: Mapped[int] = mapped_column(Integer)
    orden: Mapped[int] = mapped_column(SmallInteger)
    creada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


class Etiqueta(Base):
    __tablename__ = "etiqueta"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(30, collation="C"), unique=True)
    creada_en: Mapped[datetime] = mapped_column(TIMESTAMPTZ, server_default=func.now())


class PublicacionEtiqueta(Base):
    __tablename__ = "publicacion_etiqueta"

    publicacion_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("publicacion.id", ondelete="CASCADE"), primary_key=True
    )
    etiqueta_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("etiqueta.id", ondelete="RESTRICT"), primary_key=True
    )
    posicion: Mapped[int] = mapped_column(SmallInteger)

    etiqueta: Mapped[Etiqueta] = relationship(lazy="joined", innerjoin=True)
