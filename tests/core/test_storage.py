"""LocalStorage.get: lo que usa el M6 para leer la portada sin interpretar URLs."""

import pytest

from app.core.storage import LocalStorage, StorageError


def test_get_devuelve_lo_guardado_y_no_sale_de_la_raiz(tmp_path) -> None:
    storage = LocalStorage(tmp_path)
    storage.put("posts/1/a.webp", b"datos", "image/webp")
    assert storage.get("posts/1/a.webp") == b"datos"
    with pytest.raises(StorageError):
        storage.get("../fuera.txt")
    with pytest.raises(StorageError):
        storage.get("posts/1/no-existe.webp")
