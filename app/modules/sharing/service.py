"""Casos de uso y reglas de negocio del módulo sharing. Es su API pública.

La tarjeta no se guarda (modelo-de-datos.md §1, D18): se compone al pedirla y el navegador la
reutiliza con el ETag, que cambia cuando cambia algo de lo que la tarjeta muestra.
"""

import hashlib
import re
import threading
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.storage import Storage, StorageError, get_storage
from app.modules.identity.schemas import UsuarioActual
from app.modules.posts import service as posts
from app.modules.posts.schemas import AudienciaId, EstadoPublicacionId, TarjetaDatos
from app.modules.sharing import render
from app.modules.sharing.exceptions import SoloAutorOAdmin, SoloPublicas, TarjetaNoDisponible

# Cambiarla invalida todas las tarjetas que los navegadores tengan guardadas.
VERSION_PLANTILLA = "1"
NOMBRE_MAX = 60

# CPU compartida en Render: como mucho dos composiciones a la vez; el resto espera su turno.
_turnos = threading.BoundedSemaphore(2)


@dataclass(frozen=True)
class Tarjeta:
    contenido: bytes
    etag: str
    nombre_archivo: str


def _validar(db: Session, actual: UsuarioActual, publicacion_id: int) -> TarjetaDatos:
    """F6.1 y HU-27, en orden: existe y la puede ver, la pide el autor o un admin, está visible
    y es Pública. La visibilidad (404/403) la resuelve posts igual que en el detalle."""
    datos = posts.para_tarjeta(db, actual, publicacion_id)
    pub = datos.publicacion
    if not (pub.es_autor or actual.es_admin):
        raise SoloAutorOAdmin()
    if pub.estado.id != EstadoPublicacionId.VISIBLE:
        raise TarjetaNoDisponible()
    if pub.audiencia.id != AudienciaId.PUBLICA:
        raise SoloPublicas()
    return datos


def _etag(datos: TarjetaDatos) -> str:
    pub = datos.publicacion
    huella = "|".join(
        str(v)
        for v in (
            pub.id,
            datos.version.isoformat(),
            pub.autor.nombre_completo,
            pub.categoria.id,
            pub.categoria.nombre,
            pub.categoria.color,
            datos.portada_ruta,
            get_settings().public_web_url,
            VERSION_PLANTILLA,
        )
    )
    return '"' + hashlib.sha256(huella.encode()).hexdigest()[:32] + '"'


def nombre_archivo(titulo: str) -> str:
    """F6.3: "red-facyt-taller-de-python.jpg" (ASCII, sin tildes, recortado)."""
    ascii_ = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")
    if len(slug) > NOMBRE_MAX:  # se corta en el último guion: sin palabras a medias
        corte = slug[: NOMBRE_MAX + 1]
        slug = corte.rsplit("-", 1)[0] if "-" in corte else slug[:NOMBRE_MAX]
    return f"red-facyt-{slug or 'publicacion'}.jpg"


def enlace(publicacion_id: int) -> tuple[str, str]:
    """URL completa (para el QR) y la misma sin el esquema (para escribirla en la tarjeta)."""
    base = get_settings().public_web_url.rstrip("/")
    url = f"{base}/p/{publicacion_id}"
    partes = urlsplit(url)
    return url, f"{partes.netloc}{partes.path}"


def etag(db: Session, actual: UsuarioActual, publicacion_id: int) -> str:
    """Solo valida y calcula el ETag: un 304 no compone la imagen."""
    return _etag(_validar(db, actual, publicacion_id))


def tarjeta(
    db: Session, actual: UsuarioActual, publicacion_id: int, storage: Storage | None = None
) -> Tarjeta:
    """F6.1-F6.3: la tarjeta de 1080 x 1080 lista para Instagram, en JPEG."""
    storage = storage or get_storage()
    datos = _validar(db, actual, publicacion_id)
    pub = datos.publicacion
    portada = None
    if datos.portada_ruta:
        try:
            portada = storage.get(datos.portada_ruta)
        except StorageError:
            portada = None  # sin la portada, la plantilla de la categoría (F6.2)
    url, visible = enlace(pub.id)
    contenido = render.Contenido(
        titulo=pub.titulo,
        categoria=pub.categoria.nombre,
        color=pub.categoria.color,
        autor=pub.autor.nombre_completo,
        fecha=pub.publicada_en,
        enlace=url,
        enlace_visible=visible,
    )
    with _turnos:
        imagen = render.componer(contenido, portada)
    return Tarjeta(contenido=imagen, etag=_etag(datos), nombre_archivo=nombre_archivo(pub.titulo))
