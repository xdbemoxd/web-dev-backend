"""Errores de negocio del módulo identity (heredan de app.core.errors.AppError)."""

from app.core.errors import (
    AppError,
    ConflictError,
    ForbiddenError,
    NotFoundError,
    UnauthorizedError,
)
from app.core.imagenes import ImagenInvalida  # noqa: F401  (vive en core; se re-exporta)


class CorreoYaRegistrado(ConflictError):
    code, title = "email_taken", "El correo ya está registrado"

    def __init__(self) -> None:
        super().__init__(
            errors=[{"field": "correo", "message": "El correo ya está en uso", "type": "unique"}]
        )


class CredencialesInvalidas(UnauthorizedError):
    # Un solo mensaje para correo inexistente, contraseña incorrecta y cuenta inactiva (F1.4).
    code, title = "invalid_credentials", "Correo o contraseña incorrectos"


class SesionInvalida(UnauthorizedError):
    code, title = "invalid_session", "La sesión no es válida o ya venció"


class SolicitudPendienteExiste(ConflictError):
    code, title = "request_pending", "Ya tienes una solicitud de profesor pendiente"


class YaEsProfesor(ConflictError):
    code, title = "already_professor", "Tu cuenta ya tiene el rol de profesor o superior"


class PasswordActualIncorrecta(AppError):
    status_code, code, title = 400, "wrong_password", "La contraseña actual no coincide"

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "field": "password_actual",
                    "message": "La contraseña actual no coincide",
                    "type": "mismatch",
                }
            ]
        )


class CambioPasswordRequerido(ForbiddenError):
    code, title = "password_change_required", "Debes cambiar tu contraseña para continuar"


# ---------------------------------------------------------------- administración (M5)
class SolicitudNoEncontrada(NotFoundError):
    code, title = "request_not_found", "La solicitud de profesor no existe"


class SolicitudYaResuelta(ConflictError):
    # HU-23: una solicitud resuelta no se vuelve a resolver (también la protege un trigger).
    code, title = "request_already_resolved", "Esta solicitud ya fue resuelta"


class SolicitudNoAplica(ConflictError):
    code, title = "request_not_applicable", "El solicitante ya no es estudiante"

    def __init__(self) -> None:
        super().__init__("Su rol cambió después de pedirlo. Recházala con un motivo.")


class UsuarioNoEncontrado(NotFoundError):
    code, title = "user_not_found", "La cuenta no existe"


class AccionSobreSiMismo(ConflictError):
    """F5.4, F5.5: un administrador no se quita su rol ni se desactiva."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail, code=code, title="No puedes hacer esto con tu propia cuenta")


class UltimoAdministrador(ConflictError):
    code, title = "last_admin", "Debe quedar al menos un administrador activo"


class SinCambios(ConflictError):
    code, title = "no_changes", "La cuenta ya está en ese estado"
