"""Casos de uso y reglas de negocio del módulo identity. Es su API pública.

Cada función que escribe hace commit al terminar; el repository nunca lo hace.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import constraint_violada
from app.core.errors import AppError
from app.core.imagenes import IMAGEN_MAX_BYTES, TIPO_MIME, procesar_imagen
from app.core.pagination import Page, PageParams
from app.core.security import (
    DUMMY_HASH,
    create_access_token,
    decode_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    password_needs_rehash,
    verify_password,
)
from app.core.storage import Storage, get_storage
from app.modules.identity import repository as repo
from app.modules.identity.exceptions import (
    AccionSobreSiMismo,
    CorreoYaRegistrado,
    CredencialesInvalidas,
    PasswordActualIncorrecta,
    SesionInvalida,
    SinCambios,
    SolicitudNoAplica,
    SolicitudNoEncontrada,
    SolicitudPendienteExiste,
    SolicitudYaResuelta,
    UltimoAdministrador,
    UsuarioNoEncontrado,
    YaEsProfesor,
)
from app.modules.identity.models import EventoCuenta, SolicitudProfesor, Usuario
from app.modules.identity.schemas import (
    AutorOut,
    CambioPasswordIn,
    EstadoSolicitudId,
    EventoCuentaOut,
    FiltroSolicitud,
    FiltrosUsuarios,
    MotivoRevocacion,
    PendientesAdminOut,
    PerfilOut,
    RegistroIn,
    ResolucionOut,
    RolId,
    RolOut,
    SolicitudAdminOut,
    SolicitudDetalleOut,
    SolicitudProfesorOut,
    TipoEventoCuentaId,
    TokenOut,
    UsuarioActual,
    UsuarioAdminOut,
    UsuarioDetalleAdminOut,
    UsuarioOut,
    UsuarioResumenOut,
)

# ---------------------------------------------------------------- imágenes de perfil
FOTO_MAX_BYTES = IMAGEN_MAX_BYTES
FOTO_LADO = 512


# ---------------------------------------------------------------- registro (F1.1, F1.2)
def registrar(db: Session, datos: RegistroIn) -> UsuarioOut:
    try:
        usuario = repo.crear_usuario(
            db,
            nombre_completo=datos.nombre_completo,
            correo=datos.correo,
            password_hash=hash_password(datos.password),
        )
        if datos.soy_profesor:
            # Marcarlo no concede el rol: crea la solicitud pendiente (F1.2, HU-02).
            repo.crear_solicitud(db, usuario.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if constraint_violada(exc) == "uq_usuario_correo":
            raise CorreoYaRegistrado() from exc
        raise
    return UsuarioOut.model_validate(usuario)


# ---------------------------------------------------------------- sesión (F1.4, F1.6)
@dataclass(frozen=True)
class SesionEmitida:
    token: TokenOut
    refresh_token: str
    refresh_expira_en: datetime


def _emitir(
    db: Session, usuario: Usuario, sesion_id: uuid.UUID, expira_absoluta_en: datetime
) -> SesionEmitida:
    settings = get_settings()
    refresh = new_refresh_token()
    expira_en = min(
        datetime.now(UTC) + timedelta(hours=settings.refresh_token_hours), expira_absoluta_en
    )
    repo.crear_refresh(db, sesion_id, hash_refresh_token(refresh), expira_en)
    token = TokenOut(
        access_token=create_access_token(usuario.id, str(sesion_id)),
        expires_in=settings.access_token_minutes * 60,
        debe_cambiar_password=usuario.debe_cambiar_password,
    )
    return SesionEmitida(token=token, refresh_token=refresh, refresh_expira_en=expira_en)


def iniciar_sesion(db: Session, correo: str, password: str) -> SesionEmitida:
    usuario = repo.get_usuario_por_correo(db, correo)
    if usuario is None:
        verify_password(password, DUMMY_HASH)  # mismo tiempo de respuesta que un correo válido
        raise CredencialesInvalidas()
    if not verify_password(password, usuario.password_hash) or not usuario.activo:
        # Cuenta desactivada: mismo aviso genérico (F1.4).
        raise CredencialesInvalidas()
    if password_needs_rehash(usuario.password_hash):
        usuario.password_hash = hash_password(password)

    expira_absoluta_en = datetime.now(UTC) + timedelta(days=get_settings().session_max_days)
    sesion = repo.crear_sesion(db, usuario.id, expira_absoluta_en)
    emitida = _emitir(db, usuario, sesion.id, expira_absoluta_en)
    db.commit()
    return emitida


def renovar(db: Session, refresh_token: str) -> SesionEmitida:
    """Rota el refresh token. Si llega uno ya usado, es un reúso: se revoca la sesión (D12)."""
    token_hash = hash_refresh_token(refresh_token)
    sesion = repo.rotar_refresh(db, token_hash)
    if sesion is None:
        existente = repo.get_refresh(db, token_hash)
        if existente is not None and existente.usado_en is not None:
            repo.revocar_sesion(db, existente.sesion_id, MotivoRevocacion.REUSO)
            db.commit()
        else:
            db.rollback()
        raise SesionInvalida()

    usuario = repo.get_usuario(db, sesion.usuario_id)
    if usuario is None or not usuario.activo:
        db.rollback()
        raise SesionInvalida()
    emitida = _emitir(db, usuario, sesion.id, sesion.expira_absoluta_en)
    db.commit()
    return emitida


def cerrar_sesion(db: Session, *, sesion_id: str | None, refresh_token: str | None) -> None:
    """Revoca la sesión entera: sus refresh tokens y cualquier access token copiado (F1.6)."""
    sid: uuid.UUID | None = None
    if sesion_id:
        sid = uuid.UUID(sesion_id)
    elif refresh_token:
        existente = repo.get_refresh(db, hash_refresh_token(refresh_token))
        sid = existente.sesion_id if existente else None
    if sid is not None:
        repo.revocar_sesion(db, sid, MotivoRevocacion.LOGOUT)
        db.commit()


def autenticar(db: Session, access_token: str) -> UsuarioActual:
    """Valida el access token y revalida sesión, cuenta y rol contra la base (F1.5, F1.6)."""
    try:
        payload = decode_access_token(access_token)
        sesion_id = uuid.UUID(payload["sid"])
        usuario_id = int(payload["sub"])
    except (jwt.InvalidTokenError, ValueError, KeyError) as exc:
        raise SesionInvalida() from exc
    usuario = repo.validar_sesion(db, sesion_id, usuario_id)
    if usuario is None:
        raise SesionInvalida()
    return UsuarioActual(
        id=usuario.id,
        rol_id=RolId(usuario.rol_id),
        sesion_id=str(sesion_id),
        debe_cambiar_password=usuario.debe_cambiar_password,
    )


# ---------------------------------------------------------------- perfil (F1.7)
def _usuario(db: Session, usuario_id: int) -> Usuario:
    usuario = repo.get_usuario(db, usuario_id)
    if usuario is None:
        raise SesionInvalida()
    return usuario


def perfil(db: Session, usuario_id: int, storage: Storage | None = None) -> PerfilOut:
    usuario = _usuario(db, usuario_id)
    storage = storage or get_storage()
    solicitud = repo.ultima_solicitud(db, usuario_id)
    return PerfilOut(
        id=usuario.id,
        nombre_completo=usuario.nombre_completo,
        correo=usuario.correo,
        rol=RolOut.model_validate(usuario.rol),
        creado_en=usuario.creado_en,
        foto_url=storage.public_url(usuario.foto_ruta) if usuario.foto_ruta else None,
        debe_cambiar_password=usuario.debe_cambiar_password,
        solicitud_profesor=(
            SolicitudProfesorOut(
                estado=solicitud.estado.codigo,
                creada_en=solicitud.creada_en,
                resuelta_en=solicitud.resuelta_en,
                motivo_rechazo=solicitud.motivo_rechazo,
            )
            if solicitud
            else None
        ),
    )


def autores(db: Session, ids: list[int], storage: Storage | None = None) -> dict[int, AutorOut]:
    """Datos públicos de varios usuarios en una consulta. API pública para posts (autor)."""
    storage = storage or get_storage()
    return {
        u.id: AutorOut(
            id=u.id,
            nombre_completo=u.nombre_completo,
            rol=RolOut.model_validate(u.rol),
            foto_url=storage.public_url(u.foto_ruta) if u.foto_ruta else None,
        )
        for u in repo.get_usuarios(db, sorted(set(ids)))
    }


def buscar_por_nombre(db: Session, texto: str, limite: int = 50) -> list[int]:
    """Ids de cuentas activas por nombre (sin tildes ni mayúsculas). API pública para posts."""
    return repo.buscar_por_nombre(db, texto, limite)


def actualizar_perfil(db: Session, usuario_id: int, nombre_completo: str) -> PerfilOut:
    _usuario(db, usuario_id).nombre_completo = nombre_completo
    db.commit()
    return perfil(db, usuario_id)


def cambiar_password(db: Session, actual: UsuarioActual, datos: CambioPasswordIn) -> None:
    """Exige la contraseña actual (F1.7) y cierra las demás sesiones abiertas."""
    usuario = _usuario(db, actual.id)
    if not verify_password(datos.password_actual, usuario.password_hash):
        raise PasswordActualIncorrecta()
    if datos.password_nueva.lower() == usuario.correo.lower():
        raise AppError(
            status_code=422,
            code="validation_error",
            title="Datos inválidos",
            errors=[
                {
                    "field": "password_nueva",
                    "message": "La contraseña no puede ser igual al correo",
                    "type": "value_error",
                }
            ],
        )
    usuario.password_hash = hash_password(datos.password_nueva)
    usuario.debe_cambiar_password = False
    repo.revocar_sesiones_usuario(
        db, usuario.id, MotivoRevocacion.CAMBIO_PASSWORD, excepto=uuid.UUID(actual.sesion_id)
    )
    db.commit()


def solicitar_profesor(db: Session, actual: UsuarioActual) -> SolicitudProfesorOut:
    """Solicitud desde el perfil (F1.2). Tras un rechazo se puede volver a pedir (F5.3)."""
    if actual.rol_id in (RolId.PROFESOR, RolId.ADMINISTRADOR):
        raise YaEsProfesor()
    try:
        solicitud = repo.crear_solicitud(db, actual.id)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if constraint_violada(exc) == "uq_solicitud_profesor_pendiente":
            raise SolicitudPendienteExiste() from exc
        raise
    db.refresh(solicitud)
    return SolicitudProfesorOut(
        estado=solicitud.estado.codigo,
        creada_en=solicitud.creada_en,
        resuelta_en=None,
        motivo_rechazo=None,
    )


def cambiar_foto(
    db: Session, usuario_id: int, data: bytes, storage: Storage | None = None
) -> PerfilOut:
    storage = storage or get_storage()
    webp = procesar_imagen(data, cuadrado=FOTO_LADO)
    ruta = f"avatars/{usuario_id}/{uuid.uuid4().hex}.webp"
    storage.put(ruta, webp, TIPO_MIME)
    try:
        # El trigger tg_usuario_encolar_foto anota la foto anterior en archivo_por_borrar.
        _usuario(db, usuario_id).foto_ruta = ruta
        db.commit()
    except Exception:
        db.rollback()
        storage.delete([ruta])
        raise
    return perfil(db, usuario_id, storage)


def quitar_foto(db: Session, usuario_id: int, storage: Storage | None = None) -> PerfilOut:
    _usuario(db, usuario_id).foto_ruta = None
    db.commit()
    return perfil(db, usuario_id, storage)


# ---------------------------------------------------------------- administrador inicial (F5.10)
def crear_admin_inicial(db: Session, *, nombre: str, correo: str, password: str) -> UsuarioOut:
    try:
        usuario = repo.crear_usuario(
            db,
            nombre_completo=nombre,
            correo=correo.lower(),
            password_hash=hash_password(password),
            rol_id=RolId.ADMINISTRADOR,
            debe_cambiar_password=True,
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if constraint_violada(exc) == "uq_usuario_correo":
            raise CorreoYaRegistrado() from exc
        raise
    return UsuarioOut.model_validate(usuario)


# ---------------------------------------------------------------- administración (M5)
# Cada acción cambia la cuenta y escribe su evento_cuenta en la misma transacción: no hay
# cambio sin responsable registrado (F5.2, F5.4-F5.6). La autorización la hace el router.
_ULTIMO_ADMIN = "al menos un administrador activo"  # mensaje de trg_garantizar_admin_activo


def _es_ultimo_admin(exc: IntegrityError) -> bool:
    return _ULTIMO_ADMIN in str(exc.orig)


def _resumen(u: Usuario, storage: Storage) -> UsuarioResumenOut:
    return UsuarioResumenOut(
        id=u.id,
        nombre_completo=u.nombre_completo,
        correo=u.correo,
        rol=RolOut.model_validate(u.rol),
        activo=u.activo,
        foto_url=storage.public_url(u.foto_ruta) if u.foto_ruta else None,
    )


def _resolucion(s: SolicitudProfesor, personas: dict[int, AutorOut]) -> ResolucionOut:
    return ResolucionOut(
        id=s.id,
        estado=s.estado.codigo,
        creada_en=s.creada_en,
        resuelta_en=s.resuelta_en,
        resuelta_por=personas.get(s.resuelta_por) if s.resuelta_por else None,
        motivo_rechazo=s.motivo_rechazo,
    )


def _solicitudes_out(
    db: Session, filas: list[SolicitudProfesor], storage: Storage
) -> list[SolicitudAdminOut]:
    solicitantes = {u.id: u for u in repo.get_usuarios(db, [s.usuario_id for s in filas])}
    personas = autores(db, [s.resuelta_por for s in filas if s.resuelta_por], storage)
    previos = repo.rechazos_previos(db, filas)
    return [
        SolicitudAdminOut(
            **_resolucion(s, personas).model_dump(),
            solicitante=_resumen(solicitantes[s.usuario_id], storage),
            rechazos_previos=previos.get(s.id, 0),
            aplica=solicitantes[s.usuario_id].rol_id == RolId.ESTUDIANTE,
        )
        for s in filas
    ]


def pendientes_admin(db: Session) -> PendientesAdminOut:
    return PendientesAdminOut(solicitudes=repo.contar_solicitudes(db, EstadoSolicitudId.PENDIENTE))


def solicitudes(
    db: Session, filtro: FiltroSolicitud, params: PageParams, storage: Storage | None = None
) -> Page[SolicitudAdminOut]:
    """F5.1, HU-23: por defecto las pendientes, de la más antigua a la más reciente."""
    storage = storage or get_storage()
    estado = EstadoSolicitudId[filtro.name]
    filas = repo.listar_solicitudes(db, estado, params.offset, params.size)
    return Page[SolicitudAdminOut].build(
        _solicitudes_out(db, filas, storage), repo.contar_solicitudes(db, estado), params
    )


def solicitud(
    db: Session, solicitud_id: int, storage: Storage | None = None
) -> SolicitudDetalleOut:
    """El detalle trae el historial del solicitante: cuántas veces lo rechazaron y por qué."""
    storage = storage or get_storage()
    s = repo.get_solicitud(db, solicitud_id)
    if s is None:
        raise SolicitudNoEncontrada()
    (base,) = _solicitudes_out(db, [s], storage)
    historial = repo.solicitudes_de(db, s.usuario_id)
    personas = autores(db, [h.resuelta_por for h in historial if h.resuelta_por], storage)
    return SolicitudDetalleOut(
        **base.model_dump(), historial=[_resolucion(h, personas) for h in historial]
    )


def _resolver(
    db: Session,
    actual: UsuarioActual,
    solicitud_id: int,
    estado: EstadoSolicitudId,
    motivo: str | None = None,
) -> int:
    solicitante_id = repo.resolver_solicitud(db, solicitud_id, estado, actual.id, motivo)
    if solicitante_id is None:
        db.rollback()
        if repo.get_solicitud(db, solicitud_id) is None:
            raise SolicitudNoEncontrada()
        raise SolicitudYaResuelta()
    return solicitante_id


def aprobar_solicitud(db: Session, actual: UsuarioActual, solicitud_id: int) -> SolicitudDetalleOut:
    """F5.2: rol de profesor, solicitud aprobada y evento de cuenta, en una transacción."""
    try:
        solicitante_id = _resolver(db, actual, solicitud_id, EstadoSolicitudId.APROBADA)
        usuario = repo.get_usuario_para_modificar(db, solicitante_id)
        if usuario is None or usuario.rol_id != RolId.ESTUDIANTE:
            raise SolicitudNoAplica()
        usuario.rol_id = RolId.PROFESOR
        repo.insertar_evento(
            db,
            EventoCuenta(
                usuario_id=usuario.id,
                tipo_evento_id=TipoEventoCuentaId.CAMBIO_ROL,
                realizado_por=actual.id,
                rol_anterior_id=RolId.ESTUDIANTE,
                rol_nuevo_id=RolId.PROFESOR,
                solicitud_id=solicitud_id,
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return solicitud(db, solicitud_id)


def rechazar_solicitud(
    db: Session, actual: UsuarioActual, solicitud_id: int, motivo: str
) -> SolicitudDetalleOut:
    """F5.3: la cuenta sigue como estaba; el solicitante ve el motivo y puede volver a pedirlo."""
    try:
        _resolver(db, actual, solicitud_id, EstadoSolicitudId.RECHAZADA, motivo)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return solicitud(db, solicitud_id)


def usuarios(
    db: Session, filtros: FiltrosUsuarios, params: PageParams, storage: Storage | None = None
) -> Page[UsuarioAdminOut]:
    """F5.4: búsqueda por nombre (sin tildes) o correo, y filtros de rol y estado."""
    storage = storage or get_storage()
    filas = repo.listar_usuarios(db, filtros, params.offset, params.size)
    pendientes = repo.con_solicitud_pendiente(db, [u.id for u in filas])
    items = [
        UsuarioAdminOut(
            **_resumen(u, storage).model_dump(),
            creado_en=u.creado_en,
            solicitud_pendiente=u.id in pendientes,
        )
        for u in filas
    ]
    return Page[UsuarioAdminOut].build(items, repo.contar_usuarios(db, filtros), params)


def usuario_admin(
    db: Session, usuario_id: int, storage: Storage | None = None
) -> UsuarioDetalleAdminOut:
    storage = storage or get_storage()
    u = repo.get_usuario(db, usuario_id)
    if u is None:
        raise UsuarioNoEncontrado()
    eventos = repo.eventos_de(db, usuario_id)
    personas = autores(db, [e.realizado_por for e in eventos], storage)
    return UsuarioDetalleAdminOut(
        **_resumen(u, storage).model_dump(),
        creado_en=u.creado_en,
        solicitud_pendiente=bool(repo.con_solicitud_pendiente(db, [u.id])),
        eventos=[
            EventoCuentaOut(
                id=e.id,
                tipo=e.tipo.codigo,
                realizado_por=personas[e.realizado_por],
                rol_anterior=RolOut.model_validate(e.rol_anterior) if e.rol_anterior else None,
                rol_nuevo=RolOut.model_validate(e.rol_nuevo) if e.rol_nuevo else None,
                motivo=e.motivo,
                solicitud_id=e.solicitud_id,
                fecha=e.fecha,
            )
            for e in eventos
        ],
    )


def _cambiar_cuenta(
    db: Session, usuario_id: int, aplicar: Callable[[Usuario], EventoCuenta]
) -> UsuarioDetalleAdminOut:
    """Bloquea la cuenta, aplica el cambio, registra el evento y confirma todo junto.

    El trigger del último administrador (con advisory lock) es la red de seguridad si dos
    administradores se quitan el rol a la vez: el segundo espera, relee y falla.
    """
    try:
        usuario = repo.get_usuario_para_modificar(db, usuario_id)
        if usuario is None:
            raise UsuarioNoEncontrado()
        evento = aplicar(usuario)
        db.flush()  # los triggers de usuario corren antes de registrar el evento
        repo.insertar_evento(db, evento)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _es_ultimo_admin(exc):
            raise UltimoAdministrador() from exc
        raise
    except Exception:
        db.rollback()
        raise
    db.expire_all()
    return usuario_admin(db, usuario_id)


def cambiar_rol(
    db: Session, actual: UsuarioActual, usuario_id: int, rol: RolId, motivo: str | None
) -> UsuarioDetalleAdminOut:
    """F5.4, HU-24. Los permisos nuevos rigen desde la siguiente petición: el rol se relee de
    la base en cada una (F1.5). Sus sesiones siguen abiertas."""

    def aplicar(u: Usuario) -> EventoCuenta:
        if u.id == actual.id and rol != RolId.ADMINISTRADOR:
            raise AccionSobreSiMismo("self_demotion", "Otro administrador debe cambiar tu rol.")
        if u.rol_id == rol:
            raise SinCambios("La cuenta ya tiene ese rol.")
        anterior = u.rol_id
        u.rol_id = rol
        return EventoCuenta(
            usuario_id=u.id,
            tipo_evento_id=TipoEventoCuentaId.CAMBIO_ROL,
            realizado_por=actual.id,
            rol_anterior_id=anterior,
            rol_nuevo_id=rol,
            motivo=motivo,
        )

    return _cambiar_cuenta(db, usuario_id, aplicar)


def desactivar(
    db: Session, actual: UsuarioActual, usuario_id: int, motivo: str
) -> UsuarioDetalleAdminOut:
    """F5.5: no puede entrar ni seguir usando sus sesiones; sus publicaciones se conservan."""

    def aplicar(u: Usuario) -> EventoCuenta:
        if u.id == actual.id:
            raise AccionSobreSiMismo("self_deactivation", "No puedes desactivar tu propia cuenta.")
        if not u.activo:
            raise SinCambios("La cuenta ya está desactivada.")
        u.activo = False
        repo.revocar_sesiones_usuario(db, u.id, MotivoRevocacion.DESACTIVACION)
        return EventoCuenta(
            usuario_id=u.id,
            tipo_evento_id=TipoEventoCuentaId.DESACTIVACION,
            realizado_por=actual.id,
            motivo=motivo,
        )

    return _cambiar_cuenta(db, usuario_id, aplicar)


def reactivar(
    db: Session, actual: UsuarioActual, usuario_id: int, motivo: str | None
) -> UsuarioDetalleAdminOut:
    """F5.6: vuelve a entrar con el rol que tenía (HU-24)."""

    def aplicar(u: Usuario) -> EventoCuenta:
        if u.activo:
            raise SinCambios("La cuenta ya está activa.")
        u.activo = True
        return EventoCuenta(
            usuario_id=u.id,
            tipo_evento_id=TipoEventoCuentaId.REACTIVACION,
            realizado_por=actual.id,
            motivo=motivo,
        )

    return _cambiar_cuenta(db, usuario_id, aplicar)
