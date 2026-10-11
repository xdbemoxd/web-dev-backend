#!/bin/sh
# Arranque en producción: aplica migraciones y levanta la API.
# (El plan gratuito de Render no tiene "pre-deploy command", por eso se migra aquí.)
set -e

alembic upgrade head

# --proxy-headers: detrás de Render/Vercel, la IP real del cliente viene en X-Forwarded-For
# (la necesita el rate limit del login).
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
