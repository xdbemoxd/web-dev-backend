"""Dependencias de FastAPI propias del módulo identity.

Públicas para los demás módulos (a través de este archivo, que no importa models ni repository):

    @router.post("/publicaciones")
    def crear(usuario: UsuarioActual = Depends(get_current_user)): ...

    @router.get("/moderacion", dependencies=[Depends(require_role(RolId.ADMINISTRADOR))])
"""

from collections.abc import Callable

from fastapi import Depends, Query
from fastapi.exceptions import RequestValidationError
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.errors import ForbiddenError
from app.modules.identity import service
from app.modules.identity.exceptions import CambioPasswordRequerido, SesionInvalida
from app.modules.identity.schemas import FiltrosUsuarios, RolId, UsuarioActual

# auto_error=False: la falta de token la responde SesionInvalida con el formato problem+json.
bearer_scheme = HTTPBearer(auto_error=False, description="Access token del login (`/auth/login`)")


def _con_www_authenticate(exc: SesionInvalida) -> SesionInvalida:
    exc.headers = {"WWW-Authenticate": "Bearer"}
    return exc


def get_current_user_sin_restriccion(
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> UsuarioActual:
    """Usuario autenticado aunque tenga pendiente el cambio obligatorio de contraseña.

    Solo para el perfil, el cambio de contraseña y el cierre de sesión.
    """
    if credenciales is None:
        raise _con_www_authenticate(SesionInvalida("Inicia sesión para continuar."))
    try:
        return service.autenticar(db, credenciales.credentials)
    except SesionInvalida as exc:
        raise _con_www_authenticate(exc) from None


def get_current_user(
    usuario: UsuarioActual = Depends(get_current_user_sin_restriccion),
) -> UsuarioActual:
    """Usuario autenticado. Con `debe_cambiar_password` solo puede cambiar la contraseña (F5.10)."""
    if usuario.debe_cambiar_password:
        raise CambioPasswordRequerido()
    return usuario


def get_optional_user(
    credenciales: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> UsuarioActual | None:
    """Visitante = None (F3.7). Un token inválido también cuenta como visitante."""
    if credenciales is None:
        return None
    try:
        usuario = service.autenticar(db, credenciales.credentials)
    except SesionInvalida:
        return None
    return None if usuario.debe_cambiar_password else usuario


def require_role(*roles: RolId) -> Callable[..., UsuarioActual]:
    """Autorización por rol en el servidor, no solo ocultando botones (F1.5)."""

    def dependency(usuario: UsuarioActual = Depends(get_current_user)) -> UsuarioActual:
        if usuario.rol_id not in roles:
            raise ForbiddenError("No tienes permiso para realizar esta acción.")
        return usuario

    return dependency


# ---------------------------------------------------------------- administración (M5)
# F1.5: las pantallas de administración exigen el rol en el servidor, en cada endpoint.
solo_admin = require_role(RolId.ADMINISTRADOR)


def filtros_usuarios(
    q: str | None = Query(None, description="Parte del nombre (sin tildes) o del correo"),
    rol_id: RolId | None = Query(None, description="1 estudiante, 2 profesor, 3 administrador"),
    activo: bool | None = Query(None, description="false: solo las cuentas desactivadas"),
) -> FiltrosUsuarios:
    try:
        return FiltrosUsuarios(q=q, rol_id=rol_id, activo=activo)
    except ValidationError as exc:
        raise RequestValidationError([{**e, "loc": ("query", "q")} for e in exc.errors()]) from None
