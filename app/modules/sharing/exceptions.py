"""Errores de negocio del módulo sharing (heredan de app.core.errors.AppError)."""

from app.core.errors import ConflictError, ForbiddenError


class SoloAutorOAdmin(ForbiddenError):
    # F6.1, HU-27: la tarjeta la piden el autor o un administrador.
    code, title = "card_forbidden", "Solo el autor o un administrador generan la tarjeta"


class SoloPublicas(ConflictError):
    # F6.1: la tarjeta sale de la plataforma; respetar la audiencia elegida.
    code, title = "card_requires_public", "Solo se comparten publicaciones públicas"

    def __init__(self) -> None:
        super().__init__(
            "La tarjeta termina en una red abierta. Cambia la audiencia a Pública para generarla."
        )


class TarjetaNoDisponible(ConflictError):
    code, title = "card_post_hidden", "La publicación está oculta"

    def __init__(self) -> None:
        super().__init__("Solo se generan tarjetas de publicaciones visibles en el muro.")
