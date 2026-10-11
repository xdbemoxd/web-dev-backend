"""Esquemas Pydantic (entrada/salida de la API) del módulo identity.

`RolId`, `UsuarioActual` y los esquemas de salida son públicos: los usan los demás módulos.
"""

import re
from datetime import datetime
from enum import IntEnum, StrEnum
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    model_validator,
)


# Ids fijos de los catálogos (migración 0003). schema.sql los usa como literales en CHECK,
# índices parciales y triggers; este es el único lugar donde el código Python los nombra.
class RolId(IntEnum):
    ESTUDIANTE = 1
    PROFESOR = 2
    ADMINISTRADOR = 3


class EstadoSolicitudId(IntEnum):
    PENDIENTE = 1
    APROBADA = 2
    RECHAZADA = 3


class TipoEventoCuentaId(IntEnum):
    CAMBIO_ROL = 1
    DESACTIVACION = 2
    REACTIVACION = 3


class FiltroSolicitud(StrEnum):
    """Pestañas de la bandeja de solicitudes de profesor (F5.1)."""

    PENDIENTE = "pendiente"
    APROBADA = "aprobada"
    RECHAZADA = "rechazada"


class MotivoRevocacion(StrEnum):
    LOGOUT = "logout"
    REUSO = "reuso"
    DESACTIVACION = "desactivacion"
    CAMBIO_PASSWORD = "cambio_password"  # noqa: S105


# ---------------------------------------------------------------- reglas de campos
PASSWORD_MIN = 8
PASSWORD_MAX = 128  # argon2 con entradas enormes es un vector de abuso de CPU


def _password_valida(value: str) -> str:
    # F1.1: de 8 a 128 caracteres con al menos una letra y un dígito.
    if not re.search(r"[^\W\d_]", value) or not re.search(r"\d", value):
        raise ValueError("La contraseña debe tener al menos una letra y un número")
    return value


Password = Annotated[
    str,
    Field(min_length=PASSWORD_MIN, max_length=PASSWORD_MAX),
    AfterValidator(_password_valida),
]
NombreCompleto = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)
]


def _correo_normalizado(value: str) -> str:
    if len(value) > 254:  # RFC 5321
        raise ValueError("El correo es demasiado largo")
    return value.lower()


Correo = Annotated[EmailStr, AfterValidator(_correo_normalizado)]


def _no_es_el_correo(password: str, correo: str) -> None:
    if password.lower() == correo.lower():
        raise ValueError("La contraseña no puede ser igual al correo")


# ---------------------------------------------------------------- entrada
class RegistroIn(BaseModel):
    nombre_completo: NombreCompleto
    correo: Correo
    password: Password
    soy_profesor: bool = Field(
        default=False, description='Marca "Soy profesor o investigador" (F1.2)'
    )

    @model_validator(mode="after")
    def _password_distinta_del_correo(self) -> Self:
        _no_es_el_correo(self.password, self.correo)
        return self


class LoginIn(BaseModel):
    correo: EmailStr
    # Sin reglas de formato: una contraseña antigua que no las cumpla igual debe poder entrar.
    password: str = Field(min_length=1, max_length=PASSWORD_MAX)


class PerfilUpdateIn(BaseModel):
    nombre_completo: NombreCompleto


class CambioPasswordIn(BaseModel):
    password_actual: str = Field(min_length=1, max_length=PASSWORD_MAX)
    password_nueva: Password


# ---------------------------------------------------------------- salida
class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105
    expires_in: int = Field(description="Segundos de vigencia del access token")
    debe_cambiar_password: bool


class RolOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    codigo: str
    nombre: str


class UsuarioOut(BaseModel):
    """Datos públicos de una cuenta. Nunca incluye el hash de la contraseña (HU-01)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    nombre_completo: str
    correo: str
    rol: RolOut
    creado_en: datetime


class AutorOut(BaseModel):
    """Lo que se muestra del autor de una publicación: sin correo (lo usa posts)."""

    id: int
    nombre_completo: str
    rol: RolOut
    foto_url: str | None


class SolicitudProfesorOut(BaseModel):
    estado: str = Field(description="pendiente, aprobada o rechazada")
    creada_en: datetime
    resuelta_en: datetime | None
    motivo_rechazo: str | None


class PerfilOut(UsuarioOut):
    foto_url: str | None
    debe_cambiar_password: bool
    solicitud_profesor: SolicitudProfesorOut | None = Field(
        description="Última solicitud de profesor, si existe (F1.2, F1.7)"
    )


class UsuarioActual(BaseModel):
    """Usuario autenticado de la petición, revalidado contra la base (F1.5)."""

    id: int
    rol_id: RolId
    sesion_id: str
    debe_cambiar_password: bool

    @property
    def es_admin(self) -> bool:
        return self.rol_id == RolId.ADMINISTRADOR


# ---------------------------------------------------------------- administración (M5)
MOTIVO_MAX = 500
Motivo = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MOTIVO_MAX)
]
MotivoOpcional = Annotated[str, StringConstraints(strip_whitespace=True, max_length=MOTIVO_MAX)]


def _vacio_es_nada(value: str | None) -> str | None:
    return value or None


class RechazoIn(BaseModel):
    motivo: Motivo = Field(description="Obligatorio (F5.3). El solicitante lo ve en su perfil")


class CambioRolIn(BaseModel):
    rol_id: RolId = Field(description="1 estudiante, 2 profesor, 3 administrador")
    motivo: Annotated[MotivoOpcional | None, AfterValidator(_vacio_es_nada)] = None


class DesactivarIn(BaseModel):
    motivo: Motivo = Field(description="Obligatorio (F5.5). Queda en el historial de la cuenta")


class ReactivarIn(BaseModel):
    motivo: Annotated[MotivoOpcional | None, AfterValidator(_vacio_es_nada)] = None


class FiltrosUsuarios(BaseModel):
    q: str | None = Field(None, description="Parte del nombre (sin tildes) o del correo")
    rol_id: RolId | None = None
    activo: bool | None = None

    @model_validator(mode="after")
    def _q(self) -> Self:
        if self.q is not None:
            self.q = self.q.strip() or None
            if self.q and not 2 <= len(self.q) <= 60:
                raise ValueError("La búsqueda debe tener entre 2 y 60 caracteres")
        return self


class UsuarioResumenOut(BaseModel):
    """Datos de una cuenta para las pantallas de administración: incluye el correo."""

    id: int
    nombre_completo: str
    correo: str
    rol: RolOut
    activo: bool
    foto_url: str | None


class ResolucionOut(BaseModel):
    """Una solicitud del historial del solicitante (la actual o una anterior)."""

    id: int
    estado: str = Field(description="pendiente, aprobada o rechazada")
    creada_en: datetime
    resuelta_en: datetime | None
    resuelta_por: AutorOut | None
    motivo_rechazo: str | None


class SolicitudAdminOut(ResolucionOut):
    solicitante: UsuarioResumenOut
    rechazos_previos: int = Field(description="Cuántas solicitudes anteriores le rechazaron")
    aplica: bool = Field(
        description="false: el solicitante ya no es estudiante; solo se puede rechazar"
    )


class SolicitudDetalleOut(SolicitudAdminOut):
    historial: list[ResolucionOut] = Field(
        description="Todas sus solicitudes, de la más reciente a la más antigua"
    )


class EventoCuentaOut(BaseModel):
    """Una acción sobre la cuenta (F5.2, F5.4-F5.6). El historial no se edita."""

    id: int
    tipo: str = Field(description="cambio_rol, desactivacion o reactivacion")
    realizado_por: AutorOut
    rol_anterior: RolOut | None
    rol_nuevo: RolOut | None
    motivo: str | None
    solicitud_id: int | None = Field(description="Si el cambio de rol vino de una solicitud")
    fecha: datetime


class UsuarioAdminOut(UsuarioResumenOut):
    creado_en: datetime
    solicitud_pendiente: bool


class UsuarioDetalleAdminOut(UsuarioAdminOut):
    eventos: list[EventoCuentaOut] = Field(description="De la más reciente a la más antigua")


class PendientesAdminOut(BaseModel):
    solicitudes: int = Field(description="Solicitudes de profesor pendientes (F5.1)")
