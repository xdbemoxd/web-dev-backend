"""Acceso a datos del módulo identity: consultas SQLAlchemy. Privado.

No hace commit: la transacción la controla el servicio.
"""

import uuid
from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select, update
from sqlalchemy.orm import Session

from app.modules.identity.models import (
    EventoCuenta,
    RefreshToken,
    Sesion,
    SolicitudProfesor,
    Usuario,
)
from app.modules.identity.schemas import (
    EstadoSolicitudId,
    FiltrosUsuarios,
    MotivoRevocacion,
    RolId,
)


# ---------------------------------------------------------------- usuarios
def get_usuario(db: Session, usuario_id: int) -> Usuario | None:
    return db.get(Usuario, usuario_id)


def get_usuarios(db: Session, ids: list[int]) -> list[Usuario]:
    if not ids:
        return []
    return list(db.scalars(select(Usuario).where(Usuario.id.in_(ids))))


def _escapar(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _nombre_contiene(texto: str) -> ColumnElement[bool]:
    """La expresión es la de ix_usuario_nombre_trgm, así la búsqueda usa el índice trigram."""
    nombre = func.f_unaccent(func.lower(Usuario.nombre_completo))
    patron = func.concat("%", func.f_unaccent(func.lower(_escapar(texto))), "%")
    return nombre.like(patron, escape="\\")


def buscar_por_nombre(db: Session, texto: str, limite: int) -> list[int]:
    """Ids de cuentas activas cuyo nombre contiene `texto`, sin tildes ni mayúsculas."""
    return list(
        db.scalars(
            select(Usuario.id)
            .where(Usuario.activo, _nombre_contiene(texto))
            .order_by(Usuario.nombre_completo)
            .limit(limite)
        )
    )


def get_usuario_por_correo(db: Session, correo: str) -> Usuario | None:
    # correo es citext: la comparación no distingue mayúsculas.
    return db.scalars(select(Usuario).where(Usuario.correo == correo)).one_or_none()


def crear_usuario(
    db: Session,
    *,
    nombre_completo: str,
    correo: str,
    password_hash: str,
    rol_id: RolId = RolId.ESTUDIANTE,
    debe_cambiar_password: bool = False,
) -> Usuario:
    usuario = Usuario(
        nombre_completo=nombre_completo,
        correo=correo,
        password_hash=password_hash,
        rol_id=rol_id,
        debe_cambiar_password=debe_cambiar_password,
    )
    db.add(usuario)
    db.flush()
    db.refresh(usuario)
    return usuario


# ---------------------------------------------------------------- solicitudes de profesor
def crear_solicitud(db: Session, usuario_id: int) -> SolicitudProfesor:
    solicitud = SolicitudProfesor(usuario_id=usuario_id)
    db.add(solicitud)
    db.flush()
    return solicitud


def ultima_solicitud(db: Session, usuario_id: int) -> SolicitudProfesor | None:
    return db.scalars(
        select(SolicitudProfesor)
        .where(SolicitudProfesor.usuario_id == usuario_id)
        .order_by(SolicitudProfesor.creada_en.desc(), SolicitudProfesor.id.desc())
        .limit(1)
    ).one_or_none()


# ---------------------------------------------------------------- sesiones y tokens
def crear_sesion(db: Session, usuario_id: int, expira_absoluta_en: datetime) -> Sesion:
    sesion = Sesion(usuario_id=usuario_id, expira_absoluta_en=expira_absoluta_en)
    db.add(sesion)
    db.flush()
    return sesion


def crear_refresh(
    db: Session, sesion_id: uuid.UUID, token_hash: bytes, expira_en: datetime
) -> None:
    db.add(RefreshToken(token_hash=token_hash, sesion_id=sesion_id, expira_en=expira_en))
    db.flush()


def rotar_refresh(db: Session, token_hash: bytes) -> Sesion | None:
    """Marca el token como usado solo si nadie lo usó antes y su sesión sigue vigente.

    Es atómico: de dos renovaciones simultáneas con el mismo token, solo una recibe la sesión.
    """
    ahora = func.now()
    sesion_id = db.scalar(
        update(RefreshToken)
        .where(
            RefreshToken.token_hash == token_hash,
            RefreshToken.usado_en.is_(None),
            RefreshToken.expira_en > ahora,
            RefreshToken.sesion_id == Sesion.id,
            Sesion.revocada_en.is_(None),
            Sesion.expira_absoluta_en > ahora,
        )
        .values(usado_en=ahora)
        .returning(RefreshToken.sesion_id)
    )
    return None if sesion_id is None else db.get(Sesion, sesion_id)


def get_refresh(db: Session, token_hash: bytes) -> RefreshToken | None:
    return db.get(RefreshToken, token_hash)


def revocar_sesion(db: Session, sesion_id: uuid.UUID, motivo: MotivoRevocacion) -> None:
    db.execute(
        update(Sesion)
        .where(Sesion.id == sesion_id, Sesion.revocada_en.is_(None))
        .values(revocada_en=func.now(), motivo_revocacion=motivo.value)
    )


def revocar_sesiones_usuario(
    db: Session,
    usuario_id: int,
    motivo: MotivoRevocacion,
    excepto: uuid.UUID | None = None,
) -> None:
    stmt = update(Sesion).where(Sesion.usuario_id == usuario_id, Sesion.revocada_en.is_(None))
    if excepto is not None:
        stmt = stmt.where(Sesion.id != excepto)
    db.execute(stmt.values(revocada_en=func.now(), motivo_revocacion=motivo.value))


def validar_sesion(db: Session, sesion_id: uuid.UUID, usuario_id: int) -> Usuario | None:
    """Una consulta por petición: sesión vigente + cuenta activa + rol actual (F1.5, F1.6)."""
    return db.scalars(
        select(Usuario)
        .join(Sesion, Sesion.usuario_id == Usuario.id)
        .where(
            Sesion.id == sesion_id,
            Sesion.usuario_id == usuario_id,
            Sesion.revocada_en.is_(None),
            Sesion.expira_absoluta_en > func.now(),
            Usuario.activo,
        )
    ).one_or_none()


# ---------------------------------------------------------------- administración (M5)
def get_usuario_para_modificar(db: Session, usuario_id: int) -> Usuario | None:
    """Bloquea la cuenta: dos administradores no la cambian a la vez."""
    return db.scalars(
        select(Usuario).where(Usuario.id == usuario_id).with_for_update(of=Usuario)
    ).one_or_none()


def _condiciones_usuarios(filtros: FiltrosUsuarios) -> list[ColumnElement[bool]]:
    condiciones: list[ColumnElement[bool]] = []
    if filtros.q:
        # correo es citext: ILIKE no distingue mayúsculas.
        correo = Usuario.correo.ilike(func.concat("%", _escapar(filtros.q), "%"))
        condiciones.append(or_(_nombre_contiene(filtros.q), correo))
    if filtros.rol_id is not None:
        condiciones.append(Usuario.rol_id == filtros.rol_id)
    if filtros.activo is not None:
        condiciones.append(Usuario.activo.is_(filtros.activo))
    return condiciones


def listar_usuarios(
    db: Session, filtros: FiltrosUsuarios, offset: int, limite: int
) -> list[Usuario]:
    return list(
        db.scalars(
            select(Usuario)
            .where(*_condiciones_usuarios(filtros))
            .order_by(Usuario.creado_en.desc(), Usuario.id.desc())
            .offset(offset)
            .limit(limite)
        )
    )


def contar_usuarios(db: Session, filtros: FiltrosUsuarios) -> int:
    stmt = select(func.count()).select_from(Usuario).where(*_condiciones_usuarios(filtros))
    return db.scalar(stmt) or 0


def con_solicitud_pendiente(db: Session, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    return set(
        db.scalars(
            select(SolicitudProfesor.usuario_id).where(
                SolicitudProfesor.usuario_id.in_(ids),
                SolicitudProfesor.estado_id == EstadoSolicitudId.PENDIENTE,
            )
        )
    )


def get_solicitud(db: Session, solicitud_id: int) -> SolicitudProfesor | None:
    return db.get(SolicitudProfesor, solicitud_id)


def listar_solicitudes(
    db: Session, estado: EstadoSolicitudId, offset: int, limite: int
) -> list[SolicitudProfesor]:
    """Pendientes: la más antigua primero, para que nadie espere de más (F5.1).
    Resueltas: la resolución más reciente primero. Usa ix_solicitud_profesor_estado_creada."""
    orden = (
        (SolicitudProfesor.creada_en.asc(), SolicitudProfesor.id.asc())
        if estado == EstadoSolicitudId.PENDIENTE
        else (SolicitudProfesor.resuelta_en.desc(), SolicitudProfesor.id.desc())
    )
    return list(
        db.scalars(
            select(SolicitudProfesor)
            .where(SolicitudProfesor.estado_id == estado)
            .order_by(*orden)
            .offset(offset)
            .limit(limite)
        )
    )


def contar_solicitudes(db: Session, estado: EstadoSolicitudId) -> int:
    stmt = (
        select(func.count())
        .select_from(SolicitudProfesor)
        .where(SolicitudProfesor.estado_id == estado)
    )
    return db.scalar(stmt) or 0


def rechazos_previos(db: Session, solicitudes: list[SolicitudProfesor]) -> dict[int, int]:
    """Por solicitud: cuántas del mismo usuario, anteriores a ella, fueron rechazadas."""
    usuarios = {s.usuario_id for s in solicitudes}
    if not usuarios:
        return {}
    rechazadas = db.execute(
        select(SolicitudProfesor.usuario_id, SolicitudProfesor.creada_en).where(
            SolicitudProfesor.usuario_id.in_(usuarios),
            SolicitudProfesor.estado_id == EstadoSolicitudId.RECHAZADA,
        )
    ).all()
    return {
        s.id: sum(1 for u, f in rechazadas if u == s.usuario_id and f < s.creada_en)
        for s in solicitudes
    }


def solicitudes_de(db: Session, usuario_id: int) -> list[SolicitudProfesor]:
    return list(
        db.scalars(
            select(SolicitudProfesor)
            .where(SolicitudProfesor.usuario_id == usuario_id)
            .order_by(SolicitudProfesor.creada_en.desc(), SolicitudProfesor.id.desc())
        )
    )


def resolver_solicitud(
    db: Session,
    solicitud_id: int,
    estado: EstadoSolicitudId,
    admin_id: int,
    motivo: str | None = None,
) -> int | None:
    """Solo resuelve si sigue pendiente: de dos administradores a la vez, gana uno (HU-23).

    Devuelve el id del solicitante, o None si la solicitud no existe o ya estaba resuelta.
    """
    return db.scalar(
        update(SolicitudProfesor)
        .where(
            SolicitudProfesor.id == solicitud_id,
            SolicitudProfesor.estado_id == EstadoSolicitudId.PENDIENTE,
        )
        .values(
            estado_id=estado,
            resuelta_por=admin_id,
            resuelta_en=func.now(),
            motivo_rechazo=motivo,
        )
        .returning(SolicitudProfesor.usuario_id)
    )


def insertar_evento(db: Session, evento: EventoCuenta) -> None:
    db.add(evento)
    db.flush()


def eventos_de(db: Session, usuario_id: int) -> list[EventoCuenta]:
    """Usa ix_evento_cuenta_usuario_fecha."""
    return list(
        db.scalars(
            select(EventoCuenta)
            .where(EventoCuenta.usuario_id == usuario_id)
            .order_by(EventoCuenta.fecha.desc(), EventoCuenta.id.desc())
        )
    )
