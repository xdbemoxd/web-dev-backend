"""Cache en Redis que degrada sin romper: si Redis no responde, la app sigue sin cache."""

import functools
import json
import logging
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

import redis
from redis.exceptions import RedisError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")

_client: redis.Redis | None = None


def get_client() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=30,
        )
    return _client


def ping() -> bool:
    try:
        return bool(get_client().ping())
    except RedisError:
        return False


def get_json(key: str) -> Any | None:
    try:
        raw = get_client().get(key)
    except RedisError as exc:
        logger.warning("cache get falló para %s: %s", key, exc)
        return None
    return None if raw is None else json.loads(raw)


def set_json(key: str, value: Any, ttl: int | None = None) -> None:
    try:
        get_client().set(key, json.dumps(value), ex=ttl or get_settings().cache_default_ttl)
    except RedisError as exc:
        logger.warning("cache set falló para %s: %s", key, exc)


def delete(*keys: str) -> None:
    if not keys:
        return
    try:
        get_client().delete(*keys)
    except RedisError as exc:
        logger.warning("cache delete falló para %s: %s", keys, exc)


def cached(prefix: str, ttl: int | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Cachea el resultado (serializable a JSON) de una función según sus argumentos."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            parts = [str(a) for a in args] + [f"{k}={v}" for k, v in sorted(kwargs.items())]
            key = ":".join([prefix, *parts])
            hit = get_json(key)
            if hit is not None:
                return hit
            result = func(*args, **kwargs)
            set_json(key, result, ttl)
            return result

        return wrapper

    return decorator
