"""Casos de uso y reglas de negocio del módulo moderation. Es su API pública.

Cada acción cambia la publicación a través de `posts.service.moderar` (que no hace commit) y
guarda su fila del historial en la misma transacción: nunca queda una acción sin registrar
ni un registro sin acción (F4.6). La autorización (solo administradores) la hace el router.
"""

from sqlalchemy.orm import Session

from app.core.pagination import CursorPage, CursorParams
from app.core.storage import Storage, get_storage
from app.modules.identity import service as identity
from app.modules.identity.schemas import AutorOut, UsuarioActual
from app.modules.moderation import repository as repo
from app.modules.moderation.models import Moderacion
from app.modules.moderation.schemas import (
    DetalleModeracionOut,
    FiltrosHistorial,
    ItemLoteIn,
    LoteOut,
    OmitidaOut,
    PendientesOut,
    RegistroOut,
)
from app.modules.posts import service as posts
from app.modules.posts.exceptions import (
    ModeracionDesactualizada,
    ModeracionNoPermitida,
    PublicacionNoEncontrada,
)
from app.modules.posts.schemas import (
    AccionModeracionId,
    CatalogoOut,
    FilaBandejaOut,
    FiltrosBandeja,
)

_VERBOS = {
    AccionModeracionId.REVISAR: "la marcó como revisada",
    AccionModeracionId.OCULTAR: "la ocultó",
    AccionModeracionId.RESTAURAR: "la restauró",
    AccionModeracionId.ELIMINAR: "la eliminó",
}


# ---------------------------------------------------------------- lectura
def _registros(
    db: Session, filas: list[Moderacion], storage: Storage | None = None
) -> list[RegistroOut]:
    personas = identity.autores(
        db, [f.moderador_id for f in filas] + [f.autor_id_al_momento for f in filas], storage
    )
    return [
        RegistroOut(
            id=f.id,
            accion=CatalogoOut.model_validate(f.accion),
            motivo=f.motivo,
            moderador=personas[f.moderador_id],
            publicacion_id=f.publicacion_id,
            titulo=f.titulo_al_momento,
            autor=personas[f.autor_id_al_momento],
            fecha=f.fecha,
        )
        for f in filas
    ]


def bandeja(
    db: Session, filtros: FiltrosBandeja, params: CursorParams
) -> CursorPage[FilaBandejaOut]:
    """F4.1, HU-19."""
    return posts.bandeja(db, filtros, params)


def pendientes(db: Session) -> PendientesOut:
    return PendientesOut(total=posts.contar_sin_revisar(db), ocultas=posts.contar_ocultas(db))


def detalle(
    db: Session, actual: UsuarioActual, publicacion_id: int, storage: Storage | None = None
) -> DetalleModeracionOut:
    """F4.1 y F4.6: la publicación completa, su estado de moderación y su historial."""
    storage = storage or get_storage()
    publicacion = posts.detalle(db, publicacion_id, actual, storage)
    estado = posts.estado_moderacion(db, publicacion_id)
    return DetalleModeracionOut(
        publicacion=publicacion,
        revisada=estado.revisada,
        marca=estado.marca,
        historial=_registros(db, repo.historial_de(db, publicacion_id), storage),
    )


def historial(
    db: Session, filtros: FiltrosHistorial, params: CursorParams
) -> CursorPage[RegistroOut]:
    """F4.6, HU-22: completo, también de publicaciones ya eliminadas."""
    filas = repo.historial(db, filtros, params.cursor, params.limite)
    return CursorPage[RegistroOut].build(_registros(db, filas), params, lambda r: (r.fecha, r.id))


def moderadores(db: Session) -> list[AutorOut]:
    """Para el filtro "por administrador" del historial."""
    personas = identity.autores(db, repo.moderadores(db))
    return sorted(personas.values(), key=lambda p: p.nombre_completo.lower())


# ---------------------------------------------------------------- acciones (F4.2-F4.5)
def _conflicto(db: Session, publicacion_id: int) -> ModeracionDesactualizada:
    """Explica qué pasó, p. ej. "Ana Pérez la ocultó", para no reintentar a ciegas."""
    detalle_ = "Vuelve a cargarla para ver su estado actual."
    ultima = repo.ultima_accion(db, publicacion_id)
    if ultima is not None:
        nombre = identity.autores(db, [ultima.moderador_id])[ultima.moderador_id].nombre_completo
        verbo = _VERBOS[AccionModeracionId(ultima.accion_id)]
        detalle_ = f"Última acción de moderación: {nombre} {verbo}. {detalle_}"
    return ModeracionDesactualizada(detalle_)


def _actuar(
    db: Session,
    actual: UsuarioActual,
    publicacion_id: int,
    accion: AccionModeracionId,
    marca: str,
    motivo: str | None = None,
) -> None:
    try:
        try:
            foto = posts.moderar(db, publicacion_id, accion, marca)
        except ModeracionDesactualizada:
            raise _conflicto(db, publicacion_id) from None
        repo.insertar(
            db,
            Moderacion(
                # Al eliminar, la fila ya no existe: queda como las demás tras el SET NULL.
                publicacion_id=None if foto.eliminada else foto.publicacion_id,
                accion_id=accion,
                moderador_id=actual.id,
                motivo=motivo,
                titulo_al_momento=foto.titulo,
                autor_id_al_momento=foto.autor_id,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def revisar(
    db: Session, actual: UsuarioActual, publicacion_id: int, marca: str
) -> DetalleModeracionOut:
    """F4.2: sigue visible y sale de "Sin revisar"."""
    _actuar(db, actual, publicacion_id, AccionModeracionId.REVISAR, marca)
    return detalle(db, actual, publicacion_id)


def ocultar(
    db: Session, actual: UsuarioActual, publicacion_id: int, marca: str, motivo: str
) -> DetalleModeracionOut:
    """F4.3: sale del muro y de la búsqueda; el autor ve el motivo."""
    _actuar(db, actual, publicacion_id, AccionModeracionId.OCULTAR, marca, motivo)
    return detalle(db, actual, publicacion_id)


def restaurar(
    db: Session, actual: UsuarioActual, publicacion_id: int, marca: str
) -> DetalleModeracionOut:
    """F4.4: vuelve al muro en su fecha original y queda revisada."""
    _actuar(db, actual, publicacion_id, AccionModeracionId.RESTAURAR, marca)
    return detalle(db, actual, publicacion_id)


def eliminar(
    db: Session, actual: UsuarioActual, publicacion_id: int, marca: str, motivo: str
) -> None:
    """F4.5: definitiva. El historial conserva título, autor y motivo (HU-22)."""
    _actuar(db, actual, publicacion_id, AccionModeracionId.ELIMINAR, marca, motivo)
    db.expunge_all()


def revisar_lote(db: Session, actual: UsuarioActual, items: list[ItemLoteIn]) -> LoteOut:
    """Cada una en su propia transacción: las que cambiaron entretanto se omiten y se dicen."""
    revisadas: list[int] = []
    omitidas: list[OmitidaOut] = []
    for item in dict((i.id, i) for i in items).values():
        try:
            _actuar(db, actual, item.id, AccionModeracionId.REVISAR, item.marca)
        except (PublicacionNoEncontrada, ModeracionDesactualizada, ModeracionNoPermitida) as exc:
            omitidas.append(OmitidaOut(id=item.id, code=exc.code))
        else:
            revisadas.append(item.id)
    return LoteOut(revisadas=revisadas, omitidas=omitidas)
