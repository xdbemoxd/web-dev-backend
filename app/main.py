"""Punto de entrada de la API: `uvicorn app.main:app`."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.api import API_PREFIX, api_router
from app.core.config import get_settings
from app.core.errors import problem, register_error_handlers
from app.core.logging import RequestIdMiddleware, SecurityHeadersMiddleware, configure_logging
from app.core.rate_limit import limiter
from app.core.storage import LOCAL_MEDIA_URL


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="API REST de Red FaCyT. Todas las rutas cuelgan de `/api/v1`.",
        # Bajo /api para que también se vean a través del rewrite de Vercel.
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    register_error_handlers(app)

    app.state.limiter = limiter
    app.add_exception_handler(
        RateLimitExceeded,
        lambda _req, _exc: problem(
            429, "rate_limited", "Demasiadas solicitudes", "Espera un momento e inténtalo de nuevo."
        ),
    )

    # El orden importa: el último que se agrega es el más externo.
    app.add_middleware(SecurityHeadersMiddleware)
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    app.add_middleware(RequestIdMiddleware)

    app.include_router(api_router)

    if settings.storage_backend == "local":
        # En local los archivos se sirven desde disco; en prod los sirve Supabase Storage.
        media_dir = Path(settings.storage_local_dir)
        media_dir.mkdir(parents=True, exist_ok=True)
        app.mount(LOCAL_MEDIA_URL, StaticFiles(directory=media_dir), name="media")
    return app


app = create_app()
