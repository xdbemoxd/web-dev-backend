"""Almacenamiento de archivos: disco en local y tests, Supabase Storage en producción.

Los módulos guardan en la base solo la ruta (p. ej. `avatars/7/<uuid>.webp`) y piden la URL
pública con `public_url`. Borrar un archivo pasa por la cola `archivo_por_borrar`, que llenan los
triggers en la misma transacción que borra o reemplaza la fila (modelo-de-datos.md, D14).
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_sessionmaker

logger = logging.getLogger(__name__)

LOCAL_MEDIA_URL = "/api/v1/media"
MAX_DELETE_ATTEMPTS = 10


class StorageError(Exception):
    pass


class Storage(Protocol):
    def put(self, path: str, data: bytes, content_type: str) -> None: ...
    def get(self, path: str) -> bytes: ...
    def delete(self, paths: list[str]) -> None: ...
    def public_url(self, path: str) -> str: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _file(self, path: str) -> Path:
        file = (self.root / path).resolve()
        if not file.is_relative_to(self.root):
            raise StorageError(f"ruta fuera del almacenamiento: {path}")
        return file

    def put(self, path: str, data: bytes, content_type: str) -> None:
        file = self._file(path)
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def get(self, path: str) -> bytes:
        try:
            return self._file(path).read_bytes()
        except FileNotFoundError as exc:
            raise StorageError(f"no existe: {path}") from exc

    def delete(self, paths: list[str]) -> None:
        for path in paths:
            self._file(path).unlink(missing_ok=True)

    def public_url(self, path: str) -> str:
        return f"{LOCAL_MEDIA_URL}/{path}"


class SupabaseStorage:
    """API REST de Supabase Storage con la clave secreta del proyecto (solo en el servidor)."""

    def __init__(self, url: str, secret_key: str, bucket: str) -> None:
        self.base = url.rstrip("/") + "/storage/v1"
        self.bucket = bucket
        # Las claves nuevas (sb_secret_...) no son JWT: el gateway las lee del header apikey.
        # Las heredadas (service_role) son JWT y además van como Bearer.
        headers = {"apikey": secret_key}
        if not secret_key.startswith("sb_"):
            headers["Authorization"] = f"Bearer {secret_key}"
        self.client = httpx.Client(headers=headers, timeout=15)

    def put(self, path: str, data: bytes, content_type: str) -> None:
        res = self.client.post(
            f"{self.base}/object/{self.bucket}/{path}",
            content=data,
            headers={"Content-Type": content_type, "x-upsert": "false"},
        )
        if res.is_error:
            raise StorageError(f"subida fallida ({res.status_code}): {res.text[:200]}")

    def get(self, path: str) -> bytes:
        # Solo rutas propias del bucket, nunca una URL recibida: no hay SSRF posible.
        res = self.client.get(f"{self.base}/object/{self.bucket}/{path}")
        if res.is_error:
            raise StorageError(f"lectura fallida ({res.status_code}): {res.text[:200]}")
        return res.content

    def delete(self, paths: list[str]) -> None:
        if not paths:
            return
        res = self.client.request(
            "DELETE", f"{self.base}/object/{self.bucket}", json={"prefixes": paths}
        )
        if res.is_error:
            raise StorageError(f"borrado fallido ({res.status_code}): {res.text[:200]}")

    def public_url(self, path: str) -> str:
        return f"{self.base}/object/public/{self.bucket}/{path}"


@lru_cache
def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend == "supabase":
        return SupabaseStorage(
            settings.supabase_url, settings.supabase_secret_key, settings.storage_bucket
        )
    return LocalStorage(Path(settings.storage_local_dir))


def procesar_archivos_por_borrar(session: Session, storage: Storage | None = None) -> int:
    """Vacía la cola `archivo_por_borrar`. Se llama después del commit que la llenó.

    Si Storage falla, la fila queda con `intentos + 1` para un reintento posterior.
    Devuelve cuántos archivos se borraron.
    """
    storage = storage or get_storage()
    filas = session.execute(
        text(
            "SELECT id, ruta FROM archivo_por_borrar WHERE intentos < :max "
            "ORDER BY creado_en LIMIT 100 FOR UPDATE SKIP LOCKED"
        ),
        {"max": MAX_DELETE_ATTEMPTS},
    ).all()
    if not filas:
        session.rollback()
        return 0
    ids = [fila.id for fila in filas]
    try:
        storage.delete([fila.ruta for fila in filas])
    except (StorageError, httpx.HTTPError) as exc:
        logger.warning("no se pudieron borrar %d archivos: %s", len(filas), exc)
        session.execute(
            text("UPDATE archivo_por_borrar SET intentos = intentos + 1 WHERE id = ANY(:ids)"),
            {"ids": ids},
        )
        session.commit()
        return 0
    session.execute(text("DELETE FROM archivo_por_borrar WHERE id = ANY(:ids)"), {"ids": ids})
    session.commit()
    return len(filas)


def vaciar_cola_en_segundo_plano() -> None:
    """Para `BackgroundTasks`: usa su propia sesión, porque la de la petición ya se cerró."""
    with get_sessionmaker()() as session:
        try:
            procesar_archivos_por_borrar(session)
        except Exception:
            logger.exception("falló el vaciado de archivo_por_borrar")
