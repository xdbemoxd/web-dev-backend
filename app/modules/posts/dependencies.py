"""Dependencias de FastAPI propias del módulo posts.

El formulario de publicación llega como multipart (datos + imágenes en una sola petición, así
crear y editar son atómicos). Cada campo se declara con `Form()` y luego se valida con
`PublicacionIn`; los errores salen con el mismo formato por campo que un cuerpo JSON.
"""

from datetime import date

from fastapi import File, Form, Query, UploadFile
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from app.core.imagenes import IMAGEN_MAX_BYTES
from app.modules.posts.exceptions import DatosInvalidos
from app.modules.posts.schemas import MAX_IMAGENES, AudienciaId, FiltrosMuro, PublicacionIn


def publicacion_form(
    titulo: str = Form(description="De 3 a 150 caracteres"),
    contenido: str = Form(description="De 1 a 10 000 caracteres"),
    categoria_id: int = Form(description="Una categoría activa (`GET /categorias`)"),
    audiencia_id: int = Form(
        AudienciaId.COMUNIDAD,
        description="1 Pública, 2 Comunidad, 3 Solo profesores, 4 Solo estudiantes",
    ),
    etiquetas: list[str] = Form(
        default=[], description="Hasta 5; se guardan en minúsculas y sin espacios"
    ),
) -> PublicacionIn:
    try:
        return PublicacionIn(
            titulo=titulo,
            contenido=contenido,
            categoria_id=categoria_id,
            audiencia_id=audiencia_id,
            etiquetas=etiquetas,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None


def imagenes_form(
    imagenes: list[UploadFile] = File(
        default=[], description="JPG, PNG o WEBP de hasta 5 MB; la primera es la portada"
    ),
) -> list[bytes]:
    if len(imagenes) > MAX_IMAGENES:
        raise DatosInvalidos("imagenes", f"Máximo {MAX_IMAGENES} imágenes por publicación")
    # Un byte más del máximo basta para detectar un archivo grande sin cargarlo entero.
    return [archivo.file.read(IMAGEN_MAX_BYTES + 1) for archivo in imagenes]


def filtros_muro(
    texto: str | None = Query(
        None,
        description=(
            'Palabras a buscar, sin distinguir mayúsculas ni tildes. Admite "frase exacta", '
            "`or` y `-palabra`"
        ),
    ),
    categoria_id: int | None = Query(None, description="Incluye categorías desactivadas"),
    autor_id: int | None = Query(None, description="Id de `GET /autores/sugerencias`"),
    etiqueta: str | None = Query(None, description='Se normaliza: "Jornadas 2026" = jornadas2026'),
    desde: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
    hasta: date | None = Query(None, description="Fecha local (Caracas), inclusive"),
) -> FiltrosMuro:
    try:
        return FiltrosMuro(
            texto=texto,
            categoria_id=categoria_id,
            autor_id=autor_id,
            etiqueta=etiqueta,
            desde=desde,
            hasta=hasta,
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from None
