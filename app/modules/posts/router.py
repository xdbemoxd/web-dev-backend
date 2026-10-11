"""Endpoints HTTP del módulo posts (Módulo 2: publicaciones; Módulo 3: muro y búsqueda)."""

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.pagination import CursorPage, CursorParams, cursor_params
from app.core.rate_limit import limiter
from app.core.storage import vaciar_cola_en_segundo_plano
from app.modules.identity.dependencies import get_current_user, get_optional_user, solo_admin
from app.modules.identity.schemas import UsuarioActual
from app.modules.posts import service
from app.modules.posts.dependencies import filtros_muro, imagenes_form, publicacion_form
from app.modules.posts.schemas import (
    AutorSugeridoOut,
    CategoriaAdminOut,
    CategoriaIn,
    CategoriaOut,
    EstadoCategoriaIn,
    FiltrosMuro,
    MuroPage,
    PublicacionIn,
    PublicacionOut,
    PublicacionResumenOut,
    VisibilidadIn,
)

router = APIRouter(tags=["posts"])

_401 = {401: {"description": "Sin sesión, sesión vencida o cerrada"}}
_AUTOR = {
    **_401,
    403: {"description": "No eres el autor"},
    404: {"description": "La publicación no existe"},
}


# ---------------------------------------------------------------- catálogos del formulario
@router.get(
    "/categorias",
    response_model=list[CategoriaOut],
    summary="Categorías",
    description=(
        "F2.2: por defecto, las activas, que son las que se ofrecen al publicar. Con "
        "`incluir_inactivas`, también las desactivadas, que siguen sirviendo como filtro (F5.9)."
    ),
)
def categorias(
    incluir_inactivas: bool = Query(False), db: Session = Depends(get_db)
) -> list[CategoriaOut]:
    return service.categorias(db, incluir_inactivas)


@router.get(
    "/autores/sugerencias",
    response_model=list[AutorSugeridoOut],
    summary="Sugerir autores para el filtro del muro",
    description=(
        "F3.3. Hasta 10 autores cuyo nombre contiene el texto, sin distinguir mayúsculas ni "
        "tildes, que tienen publicaciones que puedes ver. El visitante solo ve autores de "
        "publicaciones públicas."
    ),
)
@limiter.limit("60/minute")
def sugerir_autores(
    request: Request,
    nombre: str = Query(min_length=2, max_length=60),
    usuario: UsuarioActual | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> list[AutorSugeridoOut]:
    return service.sugerir_autores(db, nombre.strip(), usuario)


@router.get(
    "/etiquetas/sugerencias",
    response_model=list[str],
    summary="Sugerir etiquetas existentes",
    description=(
        "F2.5. Hasta 10 etiquetas que empiezan por el prefijo (ya normalizado). Solo sugiere "
        "etiquetas de publicaciones que puedes ver."
    ),
    responses=_401,
)
def sugerir_etiquetas(
    prefijo: str = Query(min_length=1, max_length=60),
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[str]:
    return service.sugerir_etiquetas(db, prefijo, usuario)


# ---------------------------------------------------------------- muro (F3.1-F3.5, F3.7)
@router.get(
    "/publicaciones",
    response_model=MuroPage,
    summary="Muro, filtros y búsqueda",
    description=(
        "F3.1-F3.5. Publicaciones visibles para quien consulta (F3.2; el visitante solo ve las "
        "públicas, F3.7), de la más reciente a la más antigua, también al buscar. Los filtros se "
        "combinan. `total` llega solo en la primera página. Para la siguiente, envía `cursor` "
        "con el valor de `siguiente` y los mismos filtros. Al buscar, `extracto` trae "
        "fragmentos con `resaltado=true` en las coincidencias; se muestra como texto, nunca HTML."
    ),
    responses={422: {"description": "Filtros inválidos"}},
)
@limiter.limit("60/minute")
def muro(
    request: Request,
    filtros: FiltrosMuro = Depends(filtros_muro),
    params: CursorParams = Depends(cursor_params),
    usuario: UsuarioActual | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> MuroPage:
    return service.muro(db, usuario, filtros, params)


# ---------------------------------------------------------------- publicaciones (F2.1-F2.9)
@router.post(
    "/publicaciones",
    response_model=PublicacionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una publicación",
    description=(
        "F2.1-F2.6. Multipart: datos y hasta 5 imágenes en la misma petición. Nace visible "
        "para su audiencia. Autor y fecha los asigna el sistema. Las imágenes se guardan como "
        "WEBP sin metadatos."
    ),
    responses={**_401, 422: {"description": "Datos o imágenes inválidos"}},
)
@limiter.limit("30/hour")
def crear(
    request: Request,
    response: Response,
    datos: PublicacionIn = Depends(publicacion_form),
    archivos: list[bytes] = Depends(imagenes_form),
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicacionOut:
    publicacion = service.crear(db, usuario, datos, archivos)
    response.headers["Location"] = f"{request.url.path}/{publicacion.id}"
    return publicacion


@router.get(
    "/publicaciones/{publicacion_id}",
    response_model=PublicacionOut,
    summary="Detalle de una publicación",
    description=(
        "F3.6. Según la audiencia y el estado. El autor ve las suyas aunque estén ocultas y, "
        "si la ocultó un administrador, el motivo (F2.8)."
    ),
    responses={
        403: {"description": "Existe, pero no tienes acceso"},
        404: {"description": "No existe o fue eliminada"},
    },
)
def detalle(
    publicacion_id: int,
    usuario: UsuarioActual | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
) -> PublicacionOut:
    return service.detalle(db, publicacion_id, usuario)


@router.put(
    "/publicaciones/{publicacion_id}",
    response_model=PublicacionOut,
    summary="Editar una publicación propia",
    description=(
        "F2.7. Mismo formulario que al crear, con todos los campos. `orden_imagenes` es el "
        "orden final: ids de imágenes que se conservan y `nueva:N` para el archivo N de "
        "`imagenes` (p. ej. `nueva:0`, `12`). Las que no aparecen se borran; la primera es la "
        "portada. La fecha de publicación no cambia y queda la marca de editada."
    ),
    responses={**_AUTOR, 422: {"description": "Datos o imágenes inválidos"}},
)
@limiter.limit("60/hour")
def editar(
    request: Request,
    publicacion_id: int,
    tareas: BackgroundTasks,
    datos: PublicacionIn = Depends(publicacion_form),
    orden_imagenes: list[str] = Form(default=[]),
    archivos: list[bytes] = Depends(imagenes_form),
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicacionOut:
    publicacion, hay_borrados = service.editar(
        db, usuario, publicacion_id, datos, orden_imagenes, archivos
    )
    if hay_borrados:
        tareas.add_task(vaciar_cola_en_segundo_plano)
    return publicacion


@router.put(
    "/publicaciones/{publicacion_id}/visibilidad",
    response_model=PublicacionOut,
    summary="Ocultar o volver a mostrar una publicación propia",
    description=(
        "F2.8. Oculta la publicación del muro sin borrarla, o la vuelve a mostrar en su fecha "
        "original. No se puede cambiar si la ocultó un administrador."
    ),
    responses={**_AUTOR, 409: {"description": "Oculta por moderación"}},
)
def cambiar_visibilidad(
    publicacion_id: int,
    datos: VisibilidadIn,
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PublicacionOut:
    return service.cambiar_visibilidad(db, usuario, publicacion_id, datos.visible)


@router.delete(
    "/publicaciones/{publicacion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Eliminar una publicación propia",
    description="F2.9. Definitiva, junto con sus imágenes. La confirmación la pide la pantalla.",
    responses=_AUTOR,
)
def eliminar(
    publicacion_id: int,
    tareas: BackgroundTasks,
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    service.eliminar(db, usuario, publicacion_id)
    tareas.add_task(vaciar_cola_en_segundo_plano)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/usuarios/me/publicaciones",
    response_model=CursorPage[PublicacionResumenOut],
    summary="Mis publicaciones",
    description=(
        "F2.8. Todas las tuyas, también las ocultas, de la más reciente a la más antigua. "
        "Para la página siguiente, envía `cursor` con el valor de `siguiente`."
    ),
    responses=_401,
)
def mis_publicaciones(
    params: CursorParams = Depends(cursor_params),
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CursorPage[PublicacionResumenOut]:
    return service.mis_publicaciones(db, usuario, params)


# ---------------------------------------------------------------- categorías: administración (M5)
admin_router = APIRouter(tags=["admin"])

_ADMIN = {**_401, 403: {"description": "Solo administradores"}}
_CATEGORIA = {**_ADMIN, 404: {"description": "La categoría no existe"}}


@admin_router.get(
    "/admin/categorias",
    response_model=list[CategoriaAdminOut],
    summary="Catálogo de categorías",
    description="F5.7-F5.9. Todas, activas e inactivas, con cuántas publicaciones las usan.",
    responses=_ADMIN,
)
def categorias_admin(
    _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> list[CategoriaAdminOut]:
    return service.categorias_admin(db)


@admin_router.post(
    "/admin/categorias",
    response_model=CategoriaAdminOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una categoría",
    description="F5.7, HU-25. Nace activa. El nombre es único sin distinguir mayúsculas.",
    responses={**_ADMIN, 409: {"description": "Nombre repetido"}, 422: {"description": "Datos"}},
)
def crear_categoria(
    request: Request,
    response: Response,
    datos: CategoriaIn,
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> CategoriaAdminOut:
    categoria = service.crear_categoria(db, datos)
    response.headers["Location"] = f"{request.url.path}/{categoria.id}"
    return categoria


@admin_router.put(
    "/admin/categorias/{categoria_id}",
    response_model=CategoriaAdminOut,
    summary="Renombrar o editar una categoría",
    description="F5.8, HU-25. Las publicaciones la conservan con el nombre nuevo.",
    responses={**_CATEGORIA, 409: {"description": "Nombre repetido"}},
)
def editar_categoria(
    categoria_id: int,
    datos: CategoriaIn,
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> CategoriaAdminOut:
    return service.editar_categoria(db, categoria_id, datos)


@admin_router.put(
    "/admin/categorias/{categoria_id}/estado",
    response_model=CategoriaAdminOut,
    summary="Desactivar o reactivar una categoría",
    description=(
        "F5.9, HU-25. Desactivada no se ofrece al publicar, pero sigue en el filtro del muro y "
        "en sus publicaciones. No se borra."
    ),
    responses=_CATEGORIA,
)
def cambiar_estado_categoria(
    categoria_id: int,
    datos: EstadoCategoriaIn,
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> CategoriaAdminOut:
    return service.cambiar_estado_categoria(db, categoria_id, datos.activa)
