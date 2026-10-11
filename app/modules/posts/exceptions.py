"""Errores de negocio del módulo posts (heredan de app.core.errors.AppError)."""

from datetime import datetime

from app.core.errors import AppError, ConflictError, ForbiddenError, NotFoundError


class PublicacionNoEncontrada(NotFoundError):
    code, title = "post_not_found", "La publicación no existe o fue eliminada"


class AccesoRestringido(ForbiddenError):
    # F3.6: la publicación existe, pero su audiencia o su estado no permiten verla.
    code, title = "post_restricted", "No tienes acceso a esta publicación"


class SoloElAutor(ForbiddenError):
    # F2.7-F2.9: ni un administrador edita publicaciones ajenas; las modera en el M4.
    code, title = "author_only", "Solo el autor puede modificar esta publicación"


class OcultaPorModeracion(ConflictError):
    code, title = "hidden_by_moderation", "Un administrador ocultó esta publicación"

    def __init__(self, motivo: str | None, ocultada_en: datetime | None) -> None:
        super().__init__(
            "Solo un administrador puede volver a mostrarla."
            + (f" Motivo: {motivo}" if motivo else "")
        )
        self.motivo = motivo
        self.ocultada_en = ocultada_en


class ModeracionNoPermitida(ConflictError):
    # F4.2-F4.4: p. ej. restaurar algo que no está oculto, o revisar dos veces.
    code, title = "moderation_not_allowed", "Esa acción no aplica al estado de la publicación"


class ModeracionDesactualizada(ConflictError):
    # Otro administrador actuó, o el autor editó, desde que se cargó la publicación.
    code, title = "moderation_conflict", "La publicación cambió mientras la revisabas"


class DatosInvalidos(AppError):
    """422 con errores por campo, mismo formato que la validación de Pydantic."""

    status_code, code, title = 422, "validation_error", "Datos inválidos"

    def __init__(self, field: str, message: str, type_: str = "value_error") -> None:
        super().__init__(errors=[{"field": field, "message": message, "type": type_}])


class CategoriaNoEncontrada(NotFoundError):
    code, title = "category_not_found", "La categoría no existe"


class NombreCategoriaRepetido(ConflictError):
    # HU-25: "avisos" choca con "Avisos" (uq_categoria_nombre_ci).
    code, title = "category_name_taken", "Ya existe una categoría con ese nombre"

    def __init__(self) -> None:
        super().__init__(
            errors=[
                {
                    "field": "nombre",
                    "message": "Ya existe una categoría con ese nombre",
                    "type": "unique",
                }
            ]
        )
