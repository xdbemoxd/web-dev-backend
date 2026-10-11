"""Configuración de la aplicación leída desde variables de entorno (.env en local)."""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_DEFAULT_JWT_SECRET = "solo-para-desarrollo-local-no-usar-en-produccion"  # noqa: S105


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "prod"] = "local"
    app_name: str = "Red FaCyT API"
    log_level: str = "INFO"

    # Postgres: Docker en local, pooler de Supabase (modo sesión, puerto 5432) en producción.
    database_url: str = "postgresql+psycopg://redfacyt:redfacyt@localhost:5432/redfacyt"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # Redis: Docker en local, Upstash (rediss://) en producción.
    redis_url: str = "redis://localhost:6379/0"
    cache_default_ttl: int = 60

    # Solo hace falta en local: en producción Vercel sirve el frontend y la API en el mismo origen.
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)

    rate_limit_enabled: bool = True

    # Dirección pública del frontend: el enlace y el QR de la tarjeta para Instagram (F6.2).
    public_web_url: str = "http://localhost:5173"

    # Autenticación (Módulo 1, ADR 0004).
    jwt_secret: str = _DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 15
    # Vencimiento por inactividad: cada renovación emite un refresh token con este plazo (F1.6).
    refresh_token_hours: int = 8
    # Tope absoluto de una sesión: la rotación no lo extiende.
    session_max_days: int = 7
    refresh_cookie_name: str = "rf_refresh"
    # Secure en todos los entornos salvo que se desactive a propósito (los navegadores aceptan
    # cookies Secure en http://localhost).
    refresh_cookie_secure: bool = True

    # Almacenamiento de archivos (fotos de perfil, imágenes). En local, disco; en prod, Supabase.
    storage_backend: Literal["local", "supabase"] = "local"
    storage_local_dir: str = "media"
    storage_bucket: str = "media"
    supabase_url: str = ""
    # Clave secreta (sb_secret_...) o service_role: solo en el servidor, nunca en el frontend.
    supabase_secret_key: str = ""

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        # Supabase y Render entregan "postgres://" o "postgresql://"; SQLAlchemy necesita el driver.
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _require_real_secret_in_prod(self) -> "Settings":
        if self.app_env == "prod" and (
            self.jwt_secret == _DEFAULT_JWT_SECRET or len(self.jwt_secret) < 32
        ):
            raise ValueError("JWT_SECRET debe definirse en producción (mínimo 32 caracteres)")
        if self.app_env == "prod" and self.storage_backend != "supabase":
            # El disco de Render se borra en cada despliegue.
            raise ValueError("En producción STORAGE_BACKEND debe ser supabase")
        if self.storage_backend == "supabase" and not (
            self.supabase_url and self.supabase_secret_key
        ):
            raise ValueError("STORAGE_BACKEND=supabase requiere SUPABASE_URL y SUPABASE_SECRET_KEY")
        return self

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"


@lru_cache
def get_settings() -> Settings:
    return Settings()
