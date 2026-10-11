"""Comandos de administración del módulo identity.

    python -m app.modules.identity.cli crear-admin --correo admin@facyt.uc.edu.ve \
        --nombre "Administrador inicial"

La contraseña temporal se lee de ADMIN_PASSWORD o se pide por teclado. El administrador debe
cambiarla en su primer acceso (F5.10, HU-26).
"""

import argparse
import getpass
import os
import sys

from pydantic import ValidationError

from app.core.db import get_sessionmaker
from app.core.errors import AppError
from app.modules.identity import service
from app.modules.identity.schemas import RegistroIn


def crear_admin(correo: str, nombre: str) -> int:
    password = os.environ.get("ADMIN_PASSWORD")
    if not password:
        if not sys.stdin.isatty():
            print(
                "Sin terminal interactiva: ejecuta el comando en tu propia terminal "
                "(docker compose exec con TTY) o define ADMIN_PASSWORD.",
                file=sys.stderr,
            )
            return 1
        password = getpass.getpass("Contraseña temporal: ")
    try:
        # Mismas reglas que el registro (F1.1).
        datos = RegistroIn(nombre_completo=nombre, correo=correo, password=password)
    except ValidationError as exc:
        for err in exc.errors():
            print(f"{'.'.join(map(str, err['loc']))}: {err['msg']}", file=sys.stderr)
        return 1
    with get_sessionmaker()() as db:
        try:
            admin = service.crear_admin_inicial(
                db, nombre=datos.nombre_completo, correo=datos.correo, password=datos.password
            )
        except AppError as exc:
            print(exc.title, file=sys.stderr)
            return 1
    print(f"Administrador creado: id={admin.id} correo={admin.correo}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.modules.identity.cli")
    sub = parser.add_subparsers(dest="comando", required=True)
    admin = sub.add_parser("crear-admin", help="Crea el administrador inicial (F5.10)")
    admin.add_argument("--correo", required=True)
    admin.add_argument("--nombre", default="Administrador inicial")
    args = parser.parse_args(argv)
    return crear_admin(args.correo, args.nombre)


if __name__ == "__main__":
    sys.exit(main())
