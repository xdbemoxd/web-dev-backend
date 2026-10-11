"""Router raíz /api/v1: aquí se registra cada módulo."""

from fastapi import APIRouter

from app.modules.health.router import router as health_router
from app.modules.identity.router import admin_router as identity_admin_router
from app.modules.identity.router import router as identity_router
from app.modules.moderation.router import router as moderation_router
from app.modules.posts.router import admin_router as posts_admin_router
from app.modules.posts.router import router as posts_router
from app.modules.sharing.router import router as sharing_router

API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(health_router)
api_router.include_router(identity_router)
api_router.include_router(posts_router)
api_router.include_router(moderation_router)
api_router.include_router(sharing_router)
api_router.include_router(identity_admin_router)
api_router.include_router(posts_admin_router)
