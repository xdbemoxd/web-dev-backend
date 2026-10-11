"""Conexión a PostgreSQL con SQLAlchemy 2.0 (síncrono, driver psycopg 3)."""

from collections.abc import Iterator
from functools import lru_cache

from sqlalchemy import Engine, MetaData, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

# Nombres de restricciones deterministas: Alembic genera migraciones estables y legibles.
# column_0_N_* une todas las columnas con "_", así un UNIQUE compuesto se llama igual que
# en docs/base-de-datos/schema.sql (p. ej. uq_imagen_publicacion_publicacion_id_orden).
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_N_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # el pooler de Supabase cierra conexiones inactivas
        pool_recycle=300,
    )


@lru_cache
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """Dependencia de FastAPI: una sesión por petición, siempre cerrada al terminar."""
    session = get_sessionmaker()()
    try:
        yield session
    finally:
        session.close()


def ping_db() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def constraint_violada(exc: IntegrityError) -> str | None:
    """Nombre de la restricción que violó un INSERT/UPDATE, para traducirlo a un error por campo."""
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None)
