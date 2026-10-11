"""Endpoints HTTP del módulo identity (Módulo 1: identidad y acceso)."""

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.errors import problem
from app.core.pagination import Page, PageParams, page_params
from app.core.rate_limit import limiter
from app.core.storage import vaciar_cola_en_segundo_plano
from app.modules.identity import service
from app.modules.identity.dependencies import (
    bearer_scheme,
    filtros_usuarios,
    get_current_user,
    get_current_user_sin_restriccion,
    solo_admin,
)
from app.modules.identity.exceptions import SesionInvalida
from app.modules.identity.schemas import (
    CambioPasswordIn,
    CambioRolIn,
    DesactivarIn,
    FiltroSolicitud,
    FiltrosUsuarios,
    LoginIn,
    PendientesAdminOut,
    PerfilOut,
    PerfilUpdateIn,
    ReactivarIn,
    RechazoIn,
    RegistroIn,
    SolicitudAdminOut,
    SolicitudDetalleOut,
    SolicitudProfesorOut,
    TokenOut,
    UsuarioActual,
    UsuarioAdminOut,
    UsuarioDetalleAdminOut,
    UsuarioOut,
)

REFRESH_COOKIE_PATH = "/api/v1/auth"

router = APIRouter(tags=["identity"])

_401 = {401: {"description": "Sin sesión, sesión vencida o cerrada"}}


# ---------------------------------------------------------------- cookie del refresh token
def _poner_cookie(response: Response, emitida: service.SesionEmitida) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=emitida.refresh_token,
        expires=emitida.refresh_expira_en,
        path=REFRESH_COOKIE_PATH,
        httponly=True,  # el JavaScript del frontend no puede leerla
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


def _borrar_cookie(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=settings.refresh_cookie_secure,
        samesite="lax",
    )


# ---------------------------------------------------------------- /auth
@router.post(
    "/auth/registro",
    response_model=UsuarioOut,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una cuenta (rol estudiante)",
    description=(
        "F1.1 y F1.2. Toda cuenta nace como estudiante. Con `soy_profesor` se crea además una "
        "solicitud de profesor pendiente, que resuelve un administrador."
    ),
    responses={409: {"description": "El correo ya está registrado"}},
)
@limiter.limit("10/hour")
def registrar(request: Request, datos: RegistroIn, db: Session = Depends(get_db)) -> UsuarioOut:
    return service.registrar(db, datos)


@router.post(
    "/auth/login",
    response_model=TokenOut,
    summary="Iniciar sesión",
    description=(
        "F1.4. Devuelve el access token (15 min) y deja el refresh token en una cookie "
        "`httpOnly`. El error no indica si falló el correo o la contraseña."
    ),
    responses={401: {"description": "Credenciales inválidas o cuenta desactivada"}},
)
@limiter.limit("5/minute")
def login(
    request: Request, response: Response, datos: LoginIn, db: Session = Depends(get_db)
) -> TokenOut:
    emitida = service.iniciar_sesion(db, datos.correo, datos.password)
    _poner_cookie(response, emitida)
    return emitida.token


@router.post(
    "/auth/refresh",
    response_model=TokenOut,
    summary="Renovar el access token",
    description=(
        "F1.6. Usa la cookie del refresh token, la rota y devuelve un access token nuevo. "
        "Reusar un refresh token ya usado cierra la sesión entera."
    ),
    responses=_401,
)
@limiter.limit("30/minute")
def refresh(
    request: Request, response: Response, db: Session = Depends(get_db)
) -> TokenOut | Response:
    token = request.cookies.get(get_settings().refresh_cookie_name)
    try:
        if not token:
            raise SesionInvalida()
        emitida = service.renovar(db, token)
    except SesionInvalida as exc:
        # Una cookie que ya no sirve se borra para que el frontend no la reintente.
        error = problem(exc.status_code, exc.code, exc.title, exc.detail)
        _borrar_cookie(error)
        return error
    _poner_cookie(response, emitida)
    return emitida.token


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar sesión",
    description=(
        "F1.6. Revoca la sesión: dejan de servir su refresh token y cualquier copia del access "
        "token. Siempre responde 204 y borra la cookie."
    ),
)
def logout(
    request: Request,
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> Response:
    sesion_id = None
    if credenciales is not None:
        try:
            sesion_id = service.autenticar(db, credenciales.credentials).sesion_id
        except SesionInvalida:
            sesion_id = None
    service.cerrar_sesion(
        db,
        sesion_id=sesion_id,
        refresh_token=request.cookies.get(get_settings().refresh_cookie_name),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _borrar_cookie(response)
    return response


# ---------------------------------------------------------------- /usuarios/me (F1.7)
@router.get(
    "/usuarios/me",
    response_model=PerfilOut,
    summary="Mi perfil",
    description="Datos, rol, foto y estado de la última solicitud de profesor (F1.2, F1.7).",
    responses=_401,
)
def mi_perfil(
    usuario: UsuarioActual = Depends(get_current_user_sin_restriccion),
    db: Session = Depends(get_db),
) -> PerfilOut:
    return service.perfil(db, usuario.id)


@router.patch(
    "/usuarios/me",
    response_model=PerfilOut,
    summary="Editar mi nombre visible",
    description="F1.7. El correo y el rol no se editan desde el perfil.",
    responses=_401,
)
def editar_perfil(
    datos: PerfilUpdateIn,
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PerfilOut:
    return service.actualizar_perfil(db, usuario.id, datos.nombre_completo)


@router.put(
    "/usuarios/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cambiar mi contraseña",
    description=(
        "F1.3 y F1.7. Exige la contraseña actual. Cierra las demás sesiones abiertas; la actual "
        "sigue activa. También cumple el cambio obligatorio del administrador inicial (F5.10)."
    ),
    responses={**_401, 400: {"description": "La contraseña actual no coincide"}},
)
@limiter.limit("10/minute")
def cambiar_password(
    request: Request,
    datos: CambioPasswordIn,
    usuario: UsuarioActual = Depends(get_current_user_sin_restriccion),
    db: Session = Depends(get_db),
) -> Response:
    service.cambiar_password(db, usuario, datos)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/usuarios/me/foto",
    response_model=PerfilOut,
    summary="Cambiar mi foto de perfil",
    description=(
        "F1.7. JPG, PNG o WEBP de hasta 5 MB. Se recorta cuadrada a 512 px y se guarda como "
        "WEBP sin metadatos. La foto anterior se borra del almacenamiento."
    ),
    responses={**_401, 422: {"description": "El archivo no es una imagen válida"}},
)
def cambiar_foto(
    foto: UploadFile,
    tareas: BackgroundTasks,
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PerfilOut:
    # Se lee un byte más del máximo para detectar archivos demasiado grandes sin cargarlos enteros.
    data = foto.file.read(service.FOTO_MAX_BYTES + 1)
    perfil = service.cambiar_foto(db, usuario.id, data)
    tareas.add_task(vaciar_cola_en_segundo_plano)
    return perfil


@router.delete(
    "/usuarios/me/foto",
    response_model=PerfilOut,
    summary="Quitar mi foto de perfil",
    responses=_401,
)
def quitar_foto(
    tareas: BackgroundTasks,
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PerfilOut:
    perfil = service.quitar_foto(db, usuario.id)
    tareas.add_task(vaciar_cola_en_segundo_plano)
    return perfil


@router.post(
    "/usuarios/me/solicitud-profesor",
    response_model=SolicitudProfesorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Solicitar el rol de profesor",
    description=(
        "F1.2. Crea una solicitud pendiente que resuelve un administrador. No se puede enviar "
        "otra mientras haya una pendiente; tras un rechazo sí."
    ),
    responses={**_401, 409: {"description": "Ya hay una pendiente o ya es profesor"}},
)
def solicitar_profesor(
    usuario: UsuarioActual = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SolicitudProfesorOut:
    return service.solicitar_profesor(db, usuario)


# ---------------------------------------------------------------- administración (Módulo 5)
admin_router = APIRouter(tags=["admin"])

_ADMIN = {**_401, 403: {"description": "Solo administradores"}}
_SOLICITUD = {**_ADMIN, 404: {"description": "La solicitud no existe"}}
_CUENTA = {
    **_ADMIN,
    404: {"description": "La cuenta no existe"},
    409: {
        "description": (
            "`self_demotion` / `self_deactivation`: no sobre tu propia cuenta. `last_admin`: "
            "debe quedar un administrador activo. `no_changes`: ya estaba así."
        )
    },
}


@admin_router.get(
    "/admin/pendientes",
    response_model=PendientesAdminOut,
    summary="Contador de la sección de administración",
    description="F5.1: solicitudes de profesor pendientes.",
    responses=_ADMIN,
)
def pendientes(
    _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> PendientesAdminOut:
    return service.pendientes_admin(db)


@admin_router.get(
    "/admin/solicitudes-profesor",
    response_model=Page[SolicitudAdminOut],
    summary="Bandeja de solicitudes de profesor",
    description=(
        "F5.1, HU-23. Por defecto las pendientes, de la más antigua a la más reciente; las "
        "resueltas, de la resolución más reciente a la más antigua. `rechazos_previos` dice "
        "cuántas veces le rechazaron antes al solicitante."
    ),
    responses=_ADMIN,
)
def solicitudes(
    estado: FiltroSolicitud = Query(FiltroSolicitud.PENDIENTE),
    params: PageParams = Depends(page_params),
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> Page[SolicitudAdminOut]:
    return service.solicitudes(db, estado, params)


@admin_router.get(
    "/admin/solicitudes-profesor/{solicitud_id}",
    response_model=SolicitudDetalleOut,
    summary="Detalle de una solicitud",
    description="F5.1. Incluye el historial de solicitudes del usuario, con los motivos.",
    responses=_SOLICITUD,
)
def solicitud(
    solicitud_id: int, _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> SolicitudDetalleOut:
    return service.solicitud(db, solicitud_id)


@admin_router.post(
    "/admin/solicitudes-profesor/{solicitud_id}/aprobar",
    response_model=SolicitudDetalleOut,
    summary="Aprobar una solicitud de profesor",
    description=(
        "F5.2, HU-23. La cuenta pasa a profesor desde su siguiente acción. Se resuelve una "
        "sola vez: si otro administrador ya la resolvió, 409."
    ),
    responses={
        **_SOLICITUD,
        409: {"description": "Ya resuelta, o el solicitante ya no es estudiante"},
    },
)
def aprobar(
    solicitud_id: int,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> SolicitudDetalleOut:
    return service.aprobar_solicitud(db, usuario, solicitud_id)


@admin_router.post(
    "/admin/solicitudes-profesor/{solicitud_id}/rechazar",
    response_model=SolicitudDetalleOut,
    summary="Rechazar una solicitud de profesor",
    description="F5.3, HU-23. Motivo obligatorio; el solicitante lo ve y puede volver a pedirlo.",
    responses={
        **_SOLICITUD,
        409: {"description": "Ya resuelta"},
        422: {"description": "Falta el motivo"},
    },
)
def rechazar(
    solicitud_id: int,
    datos: RechazoIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> SolicitudDetalleOut:
    return service.rechazar_solicitud(db, usuario, solicitud_id, datos.motivo)


@admin_router.get(
    "/admin/usuarios",
    response_model=Page[UsuarioAdminOut],
    summary="Cuentas de usuario",
    description="F5.4. Busca por nombre (sin tildes) o correo; filtra por rol y estado.",
    responses={**_ADMIN, 422: {"description": "Búsqueda inválida"}},
)
def usuarios(
    filtros: FiltrosUsuarios = Depends(filtros_usuarios),
    params: PageParams = Depends(page_params),
    _: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> Page[UsuarioAdminOut]:
    return service.usuarios(db, filtros, params)


@admin_router.get(
    "/admin/usuarios/{usuario_id}",
    response_model=UsuarioDetalleAdminOut,
    summary="Detalle de una cuenta",
    description="F5.4-F5.6. Incluye el historial de cambios de la cuenta con su responsable.",
    responses={**_ADMIN, 404: {"description": "La cuenta no existe"}},
)
def usuario_admin(
    usuario_id: int, _: UsuarioActual = Depends(solo_admin), db: Session = Depends(get_db)
) -> UsuarioDetalleAdminOut:
    return service.usuario_admin(db, usuario_id)


@admin_router.put(
    "/admin/usuarios/{usuario_id}/rol",
    response_model=UsuarioDetalleAdminOut,
    summary="Cambiar el rol de una cuenta",
    description=(
        "F5.4, HU-24. Rige desde la siguiente acción del usuario. No puedes quitarte tu propio "
        "rol de administrador, y siempre debe quedar un administrador activo."
    ),
    responses=_CUENTA,
)
def cambiar_rol(
    usuario_id: int,
    datos: CambioRolIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> UsuarioDetalleAdminOut:
    return service.cambiar_rol(db, usuario, usuario_id, datos.rol_id, datos.motivo)


@admin_router.post(
    "/admin/usuarios/{usuario_id}/desactivar",
    response_model=UsuarioDetalleAdminOut,
    summary="Desactivar una cuenta",
    description=(
        "F5.5, HU-24. Motivo obligatorio. Pierde el acceso en su siguiente acción y se cierran "
        "todas sus sesiones; sus publicaciones se conservan. No puedes desactivarte."
    ),
    responses={**_CUENTA, 422: {"description": "Falta el motivo"}},
)
def desactivar(
    usuario_id: int,
    datos: DesactivarIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> UsuarioDetalleAdminOut:
    return service.desactivar(db, usuario, usuario_id, datos.motivo)


@admin_router.post(
    "/admin/usuarios/{usuario_id}/reactivar",
    response_model=UsuarioDetalleAdminOut,
    summary="Reactivar una cuenta",
    description="F5.6, HU-24. Vuelve a entrar con el rol que tenía.",
    responses=_CUENTA,
)
def reactivar(
    usuario_id: int,
    datos: ReactivarIn,
    usuario: UsuarioActual = Depends(solo_admin),
    db: Session = Depends(get_db),
) -> UsuarioDetalleAdminOut:
    return service.reactivar(db, usuario, usuario_id, datos.motivo)
