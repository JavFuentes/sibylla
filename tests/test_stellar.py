"""Tests para la logica de seleccion de noticia destacada de Stellar-View.

Cubre _select_stellar_featured: priorizacion por imagen, luego resumen, luego
no-repeticion (dias desde la ultima vez que fue destacada), luego fuente
distinta a la anterior; anclaje intra-dia; y el registro en el historial.
"""
from datetime import datetime, timezone

import pytest

from sibylla.models import NewsItem
from sibylla.web import (
    _record_stellar_featured,
    _select_stellar_featured,
)

_NOW = datetime(2026, 6, 29, 12, 0, 0, tzinfo=timezone.utc)
_TODAY = "2026-06-29"


def _item(source_id: str, *, image: str | None = None,
          published: datetime | None = None) -> NewsItem:
    """Crea un NewsItem minimo para tests."""
    return NewsItem(
        title=f"Noticia {source_id}",
        url=f"https://example.com/{source_id}",
        source_id=source_id,
        source_name=source_id.upper(),
        tier=1,
        published=published or _NOW,
        image=image,
    )


IMG = "https://img.example.com/x.jpg"


# ---------------------------------------------------------------------------
# _select_stellar_featured: prioridades basicas
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
        "sin imagen ni resumen, desempata por orden/recencia (el primero)",
    ),
    (
        [_item("nasa"), _item("esa", image=IMG)],
        {},
        "esa",
        "la imagen es la prioridad dura, aunque no sea el primero",
    ),
    (
        [_item("nasa", image=IMG), _item("esa")],
        {_item("esa").dedup_key: "resumen de esa"},
        "nasa",
        "imagen gana a resumen (imagen es mayor peso)",
    ),
    (
        [_item("nasa", image=IMG), _item("esa", image=IMG)],
        {_item("esa").dedup_key: "resumen de esa"},
        "esa",
        "empate en imagen: desempata por resumen",
    ),
]


@pytest.mark.parametrize("items,resumenes,expected_src,_desc", SELECT_CASES)
def test_select_stellar_featured(items, resumenes, expected_src, _desc):
    result = _select_stellar_featured(items, resumenes, today=_TODAY)
    if expected_src is None:
        assert result is None
    else:
        assert result is not None
        assert result.source_id == expected_src


# ---------------------------------------------------------------------------
# No-repeticion: entre dos con imagen, gana la menos destacada recientemente
# ---------------------------------------------------------------------------
def test_no_repeticion_prefiere_menos_reciente():
    nasa = _item("nasa", image=IMG)
    esa = _item("esa", image=IMG)
    # nasa fue destacada ayer; esa hace 10 dias -> gana esa.
    history = [
        {"date": "2026-06-28", "id": nasa.dedup_key, "source_id": "nasa"},
        {"date": "2026-06-19", "id": esa.dedup_key, "source_id": "esa"},
    ]
    result = _select_stellar_featured([nasa, esa], {}, history=history, today=_TODAY)
    assert result.source_id == "esa"


def test_no_repeticion_nunca_destacada_gana():
    nasa = _item("nasa", image=IMG)  # destacada ayer
    esa = _item("esa", image=IMG)    # nunca destacada
    history = [{"date": "2026-06-28", "id": nasa.dedup_key, "source_id": "nasa"}]
    result = _select_stellar_featured([nasa, esa], {}, history=history, today=_TODAY)
    assert result.source_id == "esa"


def test_no_repeticion_no_desplaza_a_imagen():
    # nasa sin imagen pero nunca destacada; esa con imagen pero destacada ayer.
    # La imagen manda: gana esa pese a repetirse.
    nasa = _item("nasa")
    esa = _item("esa", image=IMG)
    history = [{"date": "2026-06-28", "id": esa.dedup_key, "source_id": "esa"}]
    result = _select_stellar_featured([nasa, esa], {}, history=history, today=_TODAY)
    assert result.source_id == "esa"


# ---------------------------------------------------------------------------
# Fuente distinta a la anterior (menor peso, ultimo criterio real)
# ---------------------------------------------------------------------------
def test_prefiere_fuente_distinta_a_la_anterior():
    # Ambas con imagen, ninguna destacada antes, misma recencia: desempata por
    # fuente distinta a la ultima destacada (nasa).
    nasa = _item("nasa", image=IMG)
    esa = _item("esa", image=IMG)
    history = [{"date": "2026-06-28", "id": "otro-id", "source_id": "nasa"}]
    result = _select_stellar_featured([nasa, esa], {}, history=history, today=_TODAY)
    assert result.source_id == "esa"


# ---------------------------------------------------------------------------
# Anclaje intra-dia
# ---------------------------------------------------------------------------
def test_anclaje_intradia_reutiliza_si_sigue_en_pool():
    nasa = _item("nasa", image=IMG)
    esa = _item("esa", image=IMG)
    # Hoy ya se destaco nasa; aunque esa "ganaria" por no-repeticion, se ancla.
    history = [
        {"date": _TODAY, "id": nasa.dedup_key, "source_id": "nasa"},
        {"date": "2026-06-19", "id": esa.dedup_key, "source_id": "esa"},
    ]
    result = _select_stellar_featured([nasa, esa], {}, history=history, today=_TODAY)
    assert result.source_id == "nasa"


def test_anclaje_intradia_reelige_si_cayo_del_pool():
    esa = _item("esa", image=IMG)
    # Hoy se destaco nasa, pero ya no esta en el pool -> reelige entre lo que hay.
    history = [{"date": _TODAY, "id": "nasa-id", "source_id": "nasa"}]
    result = _select_stellar_featured([esa], {}, history=history, today=_TODAY)
    assert result.source_id == "esa"


# ---------------------------------------------------------------------------
# _record_stellar_featured
# ---------------------------------------------------------------------------
def test_record_anexa_entrada():
    history = [{"date": "2026-06-28", "id": "a", "source_id": "nasa"}]
    out = _record_stellar_featured(history, _TODAY, "b", "esa")
    assert out[-1] == {"date": _TODAY, "id": "b", "source_id": "esa"}
    assert len(out) == 2


def test_record_reemplaza_entrada_del_mismo_dia():
    history = [{"date": _TODAY, "id": "a", "source_id": "nasa"}]
    out = _record_stellar_featured(history, _TODAY, "b", "esa")
    assert len(out) == 1
    assert out[0] == {"date": _TODAY, "id": "b", "source_id": "esa"}


def test_record_recorta_a_max():
    from sibylla.web import STELLAR_HISTORY_MAX
    history = [{"date": f"2026-01-{i:02d}", "id": str(i), "source_id": "s"}
               for i in range(1, STELLAR_HISTORY_MAX + 5)]
    out = _record_stellar_featured(history, _TODAY, "nuevo", "esa")
    assert len(out) == STELLAR_HISTORY_MAX
    assert out[-1]["id"] == "nuevo"


# ---------------------------------------------------------------------------
# Exclusion de APOD: nunca puede ser la destacada de Stellar-View
# ---------------------------------------------------------------------------
def test_apod_excluido_de_stellar_aunque_tenga_imagen():
    """Una tarjeta APOD (source_id='apod') no puede salir destacada aunque tenga imagen,
    que es el criterio de mayor peso en _select_stellar_featured."""
    from sibylla.apod import APOD_SOURCE_ID
    apod_item = _item(APOD_SOURCE_ID, image=IMG)
    esa_item = _item("esa", image=IMG)
    # APOD tiene imagen al igual que esa, pero debe quedar excluida
    result = _select_stellar_featured([apod_item, esa_item], {}, today=_TODAY)
    assert result is not None
    assert result.source_id != APOD_SOURCE_ID
    assert result.source_id == "esa"


def test_apod_excluido_pool_con_solo_apod_devuelve_none():
    """Si el unico item en el pool es APOD, devuelve None (no hay candidatos validos)."""
    from sibylla.apod import APOD_SOURCE_ID
    apod_item = _item(APOD_SOURCE_ID, image=IMG)
    result = _select_stellar_featured([apod_item], {}, today=_TODAY)
    assert result is None


def test_select_muta_historial_via_payload():
    # build_stellar_news_payload debe registrar la destacada in place.
    from sibylla.web import build_stellar_news_payload
    nasa = _item("nasa", image=IMG)
    history: list[dict] = []
    build_stellar_news_payload(
        [nasa], site_url="https://sibylla.cl", generated_at=_NOW,
        translate=False, history=history, today=_TODAY,
    )
    assert history[-1]["id"] == nasa.dedup_key
    assert history[-1]["date"] == _TODAY
