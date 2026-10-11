"""Helpers compartidos por las pruebas de todos los módulos."""

import io
from typing import Any

from PIL import Image
from sqlalchemy import text

from app.core.db import get_engine

PASSWORD = "clave-segura-123"


def sql(query: str, **params: Any) -> Any:
    """Ejecuta SQL directo en la base de pruebas (para preparar o comprobar estados)."""
    with get_engine().begin() as conn:
        result = conn.execute(text(query), params)
        return result.all() if result.returns_rows else None


def imagen(formato: str = "PNG", tamano: tuple[int, int] = (800, 600), **save: Any) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", tamano, (30, 64, 175)).save(buffer, format=formato, **save)
    return buffer.getvalue()
