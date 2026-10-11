"""Endpoints HTTP del módulo moderation (Módulo 4). Todos exigen rol administrador (F1.5)."""

from fastapi import APIRouter, BackgroundTasks, Depends, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import CursorPage, CursorParams, cursor_params
from app.core.storage import vaciar_cola_en_segundo_plano
from app.modules.identity.schemas import AutorOut, UsuarioActual
from app.modules.moderation import service
from app.modules.moderation.dependencies import filtros_bandeja, filtros_historial, solo_admin
from app.modules.moderation.schemas import (
    AccionIn,
    DetalleModeracionOut,
    FiltrosHistorial,
    LoteIn,
    LoteOut,
    MotivoIn,
    PendientesOut,
    RegistroOut,
)
from app.modules.posts.schemas import FilaBandejaOut, FiltrosBandeja

router = APIRouter(tags=["moderation"])

_ADMIN = {
    401: {"description": "Sin sesión, sesión vencida o cerrada"},
    403: {"description": "Solo administradores"},
}
_ACCION = {
    **_ADMIN,
    404: {"description": "La publicación no existe o fue eliminada"},
    409: {
        "description": (
            "`moderation_conflict`: cambió desde que la cargaste (otro administrador o el "
            "autor). `moderation_not_allowed`: la acción no aplica a su estado."
        )
    },
}


# ---------------------------------------------------------------- bandeja (F4.1)
@router.get(
    "/moderacion/publicaciones",
    response_model=CursorPage[FilaBandejaOut],
    summary="Bandeja de moderación",
    description=(
        "F4.1, HU-19. Todas las publicaciones, de cualquier audiencia, de la más reciente a la "
        "más antigua. Por defecto, las sin revisar. Para la siguiente página, envía `cursor` con "
        "el valor de `siguiente` y los mismos filtros."
    ),
    responses={**_ADMIN, 422: {"description": "Filtros inválidos"}},
)
def bandeja(
    filtros: FiltrosBandeja = Depends(filtros_bandeja),
    params: CursorParams = Depends(cursor_params),
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> CursorPage[FilaBandejaOut]:
    return service.bandeja(db, filtros, params)


@router.get(
    "/moderacion/publicaciones/pendientes",
    response_model=PendientesOut,
    summary="Cuántas faltan por revisar",
    description="Para el indicador del menú del administrador.",
    responses=_ADMIN,
)
def pendientes(
    _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> PendientesOut:
    return service.pendientes(db)


@router.post(
    "/moderacion/publicaciones/revisar-lote",
    response_model=LoteOut,
    summary="Marcar varias como revisadas",
    description=(
        "F4.2 en lote. Cada una se registra en el historial. Las que cambiaron desde que las "
        "cargaste, o ya no existen, se omiten y se informan en `omitidas`."
    ),
    responses={**_ADMIN, 422: {"description": "De 1 a 50 publicaciones"}},
)
def revisar_lote(
    datos: LoteIn, usuario: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> LoteOut:
    return service.revisar_lote(db, usuario, datos.items)


@router.get(
    "/moderacion/publicaciones/{publicacion_id}",
    response_model=DetalleModeracionOut,
    summary="Detalle para moderar",
    description=(
        "F4.1, F4.6. La publicación completa, su estado de moderación, la `marca` que se envía "
        "con cada acción y su historial."
    ),
    responses={**_ADMIN, 404: {"description": "No existe o fue eliminada"}},
)
def detalle(
    publicacion_id: int,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> DetalleModeracionOut:
    return service.detalle(db, usuario, publicacion_id)


# ---------------------------------------------------------------- acciones (F4.2-F4.5)
@router.post(
    "/moderacion/publicaciones/{publicacion_id}/revisar",
    response_model=DetalleModeracionOut,
    summary="Marcar como revisada",
    description="F4.2. Sigue visible y sale de «Sin revisar». Si el autor la edita, vuelve.",
    responses=_ACCION,
)
def revisar(
    publicacion_id: int,
    datos: AccionIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> DetalleModeracionOut:
    return service.revisar(db, usuario, publicacion_id, datos.marca)


@router.post(
    "/moderacion/publicaciones/{publicacion_id}/ocultar",
    response_model=DetalleModeracionOut,
    summary="Ocultar con un motivo",
    description=(
        "F4.3, HU-20. Desaparece del muro y de la búsqueda. El autor la sigue viendo con el "
        "motivo, pero no puede volver a mostrarla."
    ),
    responses={**_ACCION, 422: {"description": "Falta el motivo"}},
)
def ocultar(
    publicacion_id: int,
    datos: MotivoIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> DetalleModeracionOut:
    return service.ocultar(db, usuario, publicacion_id, datos.marca, datos.motivo)


@router.post(
    "/moderacion/publicaciones/{publicacion_id}/restaurar",
    response_model=DetalleModeracionOut,
    summary="Restaurar una publicación oculta",
    description="F4.4, HU-20. Vuelve al muro en su fecha original y queda revisada.",
    responses=_ACCION,
)
def restaurar(
    publicacion_id: int,
    datos: AccionIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> DetalleModeracionOut:
    return service.restaurar(db, usuario, publicacion_id, datos.marca)


@router.post(
    "/moderacion/publicaciones/{publicacion_id}/eliminar",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar definitivamente",
    description=(
        "F4.5, HU-21. Borra la publicación y sus imágenes; no se puede deshacer. El historial "
        "conserva título, autor y motivo. La doble confirmación la pide la pantalla."
    ),
    responses={**_ACCION, 422: {"description": "Falta el motivo"}},
)
def eliminar(
    publicacion_id: int,
    datos: MotivoIn,
    tareas: BackgroundTasks,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> Response:
    service.eliminar(db, usuario, publicacion_id, datos.marca, datos.motivo)
    tareas.add_task(vaciar_cola_en_segundo_plano)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------- historial (F4.6)
@router.get(
    "/moderacion/historial",
    response_model=CursorPage[RegistroOut],
    summary="Historial de moderación",
    description=(
        "F4.6, HU-22. Todas las acciones, de la más reciente a la más antigua, también las de "
        "publicaciones eliminadas (`publicacion_id` null). No se edita ni se borra."
    ),
    responses={**_ADMIN, 422: {"description": "Filtros inválidos"}},
)
def historial(
    filtros: FiltrosHistorial = Depends(filtros_historial),
    params: CursorParams = Depends(cursor_params),
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> CursorPage[RegistroOut]:
    return service.historial(db, filtros, params)


@router.get(
    "/moderacion/moderadores",
    response_model=list[AutorOut],
    summary="Administradores que aparecen en el historial",
    description="Para el filtro «por administrador» del historial.",
    responses=_ADMIN,
)
def moderadores(
    _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> list[AutorOut]:
    return service.moderadores(db)
