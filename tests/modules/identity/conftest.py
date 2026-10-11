"""Pruebas del módulo identity: cada una empieza con la base vacía."""

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _clean(clean_db: None) -> Iterator[None]:
    yield
