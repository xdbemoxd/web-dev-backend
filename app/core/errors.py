"""Formato único de errores (RFC 9457, application/problem+json) y sus handlers."""

import logging
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

logger = logging.getLogger(__name__)

PROBLEM_JSON = "application/problem+json"


class AppError(Exception):
    """Error de negocio. Los módulos heredan de aquí (p. ej. EmailAlreadyRegistered)."""

    status_code: int = 400
    code: str = "bad_request"
    title: str = "Solicitud inválida"

    def __init__(
        self,
        detail: str | None = None,
        *,
        status_code: int | None = None,
        code: str | None = None,
        title: str | None = None,
        errors: list[dict[str, Any]] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code or self.status_code
        self.code = code or self.code
        self.title = title or self.title
        self.detail = detail
        self.errors = errors or []
        self.headers = headers or {}
        super().__init__(detail or self.title)


class NotFoundError(AppError):
    status_code, code, title = 404, "not_found", "Recurso no encontrado"


class ConflictError(AppError):
    status_code, code, title = 409, "conflict", "Conflicto con el estado actual"


class UnauthorizedError(AppError):
    status_code, code, title = 401, "unauthorized", "Autenticación requerida"


class ForbiddenError(AppError):
    status_code, code, title = 403, "forbidden", "Acceso no permitido"


def problem(
    status: int,
    code: str,
    title: str,
    detail: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://red-facyt/errors/{code}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": request_id_var.get(),
    }
    if detail:
        body["detail"] = detail
    if errors:
        body["errors"] = errors
    return JSONResponse(body, status_code=status, media_type=PROBLEM_JSON)


def _field_path(loc: tuple[Any, ...]) -> str:
    # ("body", "email") -> "email"; ("query", "page") -> "page"
    parts = [str(p) for p in loc if p not in ("body", "query", "path", "header", "cookie")]
    return ".".join(parts) or str(loc[-1])


async def _app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    response = problem(exc.status_code, exc.code, exc.title, exc.detail, exc.errors)
    response.headers.update(exc.headers)
    return response


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": _field_path(tuple(err["loc"])), "message": err["msg"], "type": err["type"]}
        for err in exc.errors()
    ]
    return problem(422, "validation_error", "Datos inválidos", errors=errors)


async def _http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    title = HTTPStatus(exc.status_code).phrase
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail != title else None
    response = problem(exc.status_code, title.lower().replace(" ", "_"), title, detail)
    if exc.headers:
        response.headers.update(exc.headers)
    return response


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("error no controlado", exc_info=exc)
    return problem(500, "internal_error", "Error interno del servidor")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
