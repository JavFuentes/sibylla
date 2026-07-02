"""Tests de la lógica PURA de la selección Nacional (sibylla/nacional.py).

Cubre (sin red ni LLM):
  - _parse_ids     (extrae el array JSON de ids del juez; robusto a fences/rango)
  - _apply_quota   (tope por medio + cuota regional mínima, preserva prioridad)
  - is_nacional / _is_regional / _outlet_key

El juez LLM (_judge) y select_nacional end-to-end NO se testean aquí (requieren
red/LLM), igual que el resto de fetchers (ver TEST.md).
"""
import pytest

from sibylla.models import NewsItem
from sibylla.nacional import (
    _apply_quota, _is_regional, _outlet_key, _parse_ids, _separate_front,
    is_nacional,
)


# ---------------------------------------------------------------------------
# _parse_ids
# ---------------------------------------------------------------------------
PARSE_CASES = [
    ("[3, 0, 7]", 10, [3, 0, 7], "array JSON limpio"),
    ("```json\n[1, 2]\n```", 10, [1, 2], "envuelto en fence markdown"),
    ("Los elegidos son: [4, 1, 2].", 10, [4, 1, 2], "array embebido en prosa"),
    ("[1, 1, 2]", 10, [1, 2], "deduplica ids repetidos"),
    ("[0, 99, 3]", 5, [0, 3], "descarta ids fuera de rango"),
    ('[{"id": 2}, {"id": 5}]', 10, [2, 5], "ids envueltos en objetos"),
    ("sin json aquí", 5, [], "sin array -> vacío"),
    ("[]", 5, [], "array vacío"),
]


@pytest.mark.parametrize("text,n_max,expected,_desc", PARSE_CASES)
def test_parse_ids(text, n_max, expected, _desc):
    assert _parse_ids(text, n_max) == expected


# ---------------------------------------------------------------------------
# helpers de clasificación
# ---------------------------------------------------------------------------
def _item(source_id, scope, publisher=None):
    extra = {"scope": scope}
    if publisher:
        extra["publisher"] = publisher
    return NewsItem(
        title=f"Noticia de {source_id}", url=f"https://{source_id}.cl/x",
        source_id=source_id, source_name=source_id, tier=2,
        topics=["nacional"], extra=extra,
    )


def test_is_nacional_true():
    assert is_nacional(_item("ciper", "national")) is True


def test_is_nacional_false_other_topic():
    it = NewsItem(title="x", url="u", source_id="s", source_name="S", tier=2, topics=["ai"])
    assert is_nacional(it) is False


def test_is_regional():
    assert _is_regional(_item("lavoz_pucon", "regional")) is True


def test_outlet_key_prefers_publisher():
    """Los ítems de Google News comparten source_id; el publisher los distingue."""
    it = _item("google_news_nacional", "regional", publisher="El Nortero")
    assert _outlet_key(it) == "el nortero"


def test_outlet_key_falls_back_to_source_id():
    assert _outlet_key(_item("ciper", "national")) == "ciper"


# ---------------------------------------------------------------------------
# _apply_quota
# ---------------------------------------------------------------------------
def test_apply_quota_length():
    ordered = [_item(f"nac{i}", "national") for i in range(6)] + \
              [_item(f"reg{i}", "regional") for i in range(4)]
    assert len(_apply_quota(ordered, n=6, min_regional=2, max_per_outlet=2)) == 6


def test_apply_quota_guarantees_min_regional():
    """Aunque los mejores 6 sean nacionales, se garantizan 2 regionales."""
    ordered = [_item(f"nac{i}", "national") for i in range(6)] + \
              [_item(f"reg{i}", "regional") for i in range(2)]
    out = _apply_quota(ordered, n=6, min_regional=2, max_per_outlet=2)
    assert sum(1 for it in out if _is_regional(it)) == 2


def test_apply_quota_respects_max_per_outlet():
    """Un mismo medio (mismo source_id) no aporta más de max_per_outlet."""
    ordered = [_item("ciper", "national") for _ in range(4)] + \
              [_item(f"reg{i}", "regional") for i in range(4)]
    out = _apply_quota(ordered, n=6, min_regional=2, max_per_outlet=2)
    assert sum(1 for it in out if it.source_id == "ciper") <= 2


def test_apply_quota_keeps_priority_order():
    """El resultado preserva el orden de prioridad de entrada (no lo baraja)."""
    ordered = [_item(f"reg{i}", "regional") for i in range(3)] + \
              [_item(f"nac{i}", "national") for i in range(3)]
    out = _apply_quota(ordered, n=4, min_regional=2, max_per_outlet=2)
    idx = [ordered.index(it) for it in out]
    assert idx == sorted(idx)


# ---------------------------------------------------------------------------
# _separate_front
# ---------------------------------------------------------------------------
def _outlets(items):
    return [_outlet_key(it) for it in items]


SEPARATE_CASES = [
    (["a", "a", "b", "c", "d", "e"], ["a", "b", "a", "c", "d", "e"],
     "2 primeras mismo medio -> intercambia la 2.ª por la 1.ª distinta"),
    (["a", "b", "c", "d"], ["a", "b", "c", "d"], "ya distintas -> sin cambios"),
    (["a", "a", "a"], ["a", "a", "a"], "todo un mismo medio -> sin cambios (imposible)"),
    (["a", "a", "a", "b"], ["a", "b", "a", "a"], "encuentra la 1.ª distinta (pos 3) y la sube"),
    (["a", "b"], ["a", "b"], "<3 items -> sin cambios"),
    (["a"], ["a"], "1 item -> sin cambios"),
]


@pytest.mark.parametrize("outlets_in,expected,_desc", SEPARATE_CASES)
def test_separate_front(outlets_in, expected, _desc):
    out = _separate_front([_item(o, "national") for o in outlets_in])
    assert _outlets(out) == expected


def test_separate_front_preserves_set():
    """Solo reordena: el conjunto de items y el conteo regional no cambian."""
    ordered = [_item("a", "regional"), _item("a", "national"),
               _item("b", "national"), _item("c", "national"),
               _item("d", "national"), _item("e", "national")]
    out = _separate_front(ordered)
    assert {id(it) for it in out} == {id(it) for it in ordered}
    assert sum(1 for it in out if _is_regional(it)) == \
        sum(1 for it in ordered if _is_regional(it))


def test_separate_front_distinguishes_by_publisher():
    """Google News: 2 items del mismo publisher no encabezan juntos."""
    a1 = _item("google_news_nacional", "national", publisher="El Nortero")
    a2 = _item("google_news_nacional", "national", publisher="El Nortero")
    b = _item("google_news_nacional", "national", publisher="El Observatodo")
    out = _separate_front([a1, a2, b])
    assert _outlet_key(out[0]) != _outlet_key(out[1])
    assert _outlet_key(out[1]) == "el observatodo"
