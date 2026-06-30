"""Tests para la logica de seleccion de noticia destacada de Stellar-View.

Cubre _select_stellar_featured: priorizacion por resumen, luego imagen,
luego orden original, y fallback cuando la lista esta vacia.
"""
from datetime import datetime, timezone

import pytest

from sibylla.models import NewsItem
from sibylla.web import _select_stellar_featured

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)


def _item(source_id: str, *, image: str | None = None) -> NewsItem:
    """Crea un NewsItem minimo para tests."""
    return NewsItem(
        title=f"Noticia {source_id}",
        url=f"https://example.com/{source_id}",
        source_id=source_id,
        source_name=source_id.upper(),
        tier=1,
        published=_NOW,
        image=image,
    )


# ---------------------------------------------------------------------------
# _select_stellar_featured
# ---------------------------------------------------------------------------
SELECT_CASES = [
    # (items, resumenes, expected_source_id, descripcion)
    (
        [],
        {},
        None,
        "lista vacia devuelve None",
    ),
    (
        [_item("nasa"), _item("esa")],
        {},
        "nasa",
        "sin resumen ni imagen, devuelve el primero",
    ),
    (
        [_item("nasa"), _item("esa")],
        {_item("esa").dedup_key: "resumen de esa"},
        "esa",
        "prefiere el item con resumen aunque no sea el primero",
    ),
    (
        [_item("nasa", image="https://img.example.com/nasa.jpg"), _item("esa")],
        {},
        "nasa",
        "sin resumen, prefiere el que tiene imagen",
    ),
    (
        [
            _item("nasa", image="https://img.example.com/nasa.jpg"),
            _item("esa"),
        ],
        {_item("esa").dedup_key: "resumen de esa"},
        "esa",
        "resumen tiene mas prioridad que imagen",
    ),
    (
        [
            _item("nasa", image="https://img.example.com/nasa.jpg"),
            _item("esa", image="https://img.example.com/esa.jpg"),
        ],
        {_item("esa").dedup_key: "resumen de esa"},
        "esa",
        "resumen mas imagen gana a solo imagen",
    ),
]


@pytest.mark.parametrize("items,resumenes,expected_src,_desc", SELECT_CASES)
def test_select_stellar_featured(items, resumenes, expected_src, _desc):
    result = _select_stellar_featured(items, resumenes)
    if expected_src is None:
        assert result is None
    else:
        assert result is not None
        assert result.source_id == expected_src
