"""Endpoints HTTP del módulo sharing (Módulo 6: tarjeta para Instagram)."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.rate_limit import limiter
from app.modules.identity.dependencies import get_current_user
from app.modules.identity.schemas import UsuarioActual
from app.modules.sharing import service

router = APIRouter(tags=["sharing"])


@router.get(
    "/publicaciones/{publicacion_id}/tarjeta",
    summary="Tarjeta para Instagram",
    description=(
        "F6.1-F6.3, HU-27. JPEG de 1080 x 1080 con la portada (o la plantilla de la categoría "
        "si no tiene imágenes), el título, la categoría, la fecha, la marca y el enlace con QR. "
        "Solo para publicaciones visibles con audiencia Pública, y la piden el autor o un "
        "administrador. Sin `descargar` se muestra como vista previa; con `descargar=true` se "
        "descarga con un nombre que incluye el título. Envía `If-None-Match` con el `ETag` "
        "para recibir 304 si no cambió."
    ),
    response_class=Response,
    responses={
        200: {"content": {"image/jpeg": {}}, "description": "La tarjeta"},
        304: {"description": "No cambió desde el ETag enviado"},
        401: {"description": "Sin sesión"},
        403: {"description": "No eres el autor ni administrador, o no puedes ver la publicación"},
        404: {"description": "La publicación no existe"},
        409: {"description": "`card_requires_public` o `card_post_hidden`"},
        429: {"description": "Demasiadas tarjetas seguidas"},
    },
)
@limiter.limit("20/minute")
def tarjeta(
    request: Request,
    publicacion_id: int,
    descargar: bool = Query(False, description="true: Content-Disposition attachment (F6.3)"),
    if_none_match: str | None = Header(None),
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    # Privada: exige sesión, así que solo la guarda el navegador de quien la pidió.
    cabeceras = {"Cache-Control": "private, max-age=0, must-revalidate"}
    if if_none_match:
        etag = service.etag(db, usuario, publicacion_id)
        if etag in {e.strip() for e in if_none_match.split(",")}:
            return Response(status_code=304, headers={**cabeceras, "ETag": etag})
    resultado = service.tarjeta(db, usuario, publicacion_id)
    disposicion = "attachment" if descargar else "inline"
    nombre = resultado.nombre_archivo
    return Response(
        content=resultado.contenido,
        media_type="image/jpeg",
        headers={
            **cabeceras,
            "ETag": resultado.etag,
            "Content-Disposition": (
                f"{disposicion}; filename=\"{nombre}\"; filename*=UTF-8''{quote(nombre)}"
            ),
        },
    )
