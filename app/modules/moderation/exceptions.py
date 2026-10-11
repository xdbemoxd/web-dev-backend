"""Errores de negocio del módulo moderation (heredan de app.core.errors.AppError).

Los de la publicación (no existe, cambió, la acción no aplica) los lanza `posts.service`,
que es quien conoce su estado.
"""
