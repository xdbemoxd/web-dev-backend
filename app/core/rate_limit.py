"""Límite de peticiones con slowapi sobre Redis. Se aplica por endpoint, p. ej. en el login (M1):

@router.post("/login")
@limiter.limit("5/minute")
def login(request: Request, ...): ...
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import get_settings

_settings = get_settings()

limiter = Limiter(
    key_func=get_remote_address,  # uvicorn corre con --proxy-headers: es la IP real del cliente
    storage_uri=_settings.redis_url,
    enabled=_settings.rate_limit_enabled,
    # Si Redis cae, se usa memoria local en vez de rechazar las peticiones.
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)
