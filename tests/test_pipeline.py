"""Tests para el núcleo del pipeline: dedupe, ranking, diversificación.

Cubre:
  - dedupe      (fusión por URL/título, conserva menor tier, mergea temas)
  - _score      (fórmula de ranking: peso tier + frescura + bonus HN)
  - rank        (orden descendente por score)
  - diversify   (límite de ítems por fuente+tema)
"""
from datetime import datetime, timezone

import pytest

from sibylla.models import NewsItem
from sibylla.pipeline import MAX_PER_SOURCE_TOPIC, dedupe, diversify, rank, _score


# --- helpers ----------------------------------------------------------------

def _it(title="T", url="https://x.com/1", source_id="s", source_name="S",
        tier=2, topics=None, published=None, extra=None):
    return NewsItem(
        title=title, url=url, source_id=source_id, source_name=source_name,
        tier=tier, topics=topics or [], published=published, extra=extra or {},
    )


# --- dedupe -----------------------------------------------------------------

DEDUPE_CASES = [
    ([], 0, None, None, "lista vacía"),
    ([_it()], 1, 2, [], "ítem único"),
    (
        [_it(url="https://a.com"), _it(url="https://b.com")],
        2, None, None, "URLs distintas → ambos conservados",
    ),
    (
        [_it(url="https://a.com", tier=3), _it(url="https://a.com", tier=1)],
        1, 1, [], "misma URL: gana tier 1 sobre tier 3",
    ),
    (
        [_it(url="https://a.com", tier=2),
         _it(url="https://a.com", tier=2)],
        1, 2, [], "mismo tier: conserva el primero encontrado",
    ),
    (
        [_it(url="https://a.com", topics=["ai"]),
         _it(url="https://a.com", topics=["physics"])],
        1, None, ["ai", "physics"], "fusiona temas de duplicados",
    ),
    (
        [_it(url="https://a.com", topics=["physics", "ai"]),
         _it(url="https://a.com", topics=["ai"])],
        1, None, ["ai", "physics"], "temas solapados → union sorted sin dupes",
    ),
    (
        [_it(url="", title="X"), _it(url="", title="Y")],
        2, None, None, "sin URL y títulos distintos → keys diferentes",
    ),
    (
        [_it(url="", title="Same Title", tier=3, topics=["z"]),
         _it(url="", title="Same Title", tier=1, topics=["a"])],
        1, 1, ["a", "z"], "mismo título sin URL: gana tier 1, mergea temas",
    ),
    (
        [_it(url="https://a.com", tier=2),
         _it(url="https://b.com", tier=1),
         _it(url="https://a.com", tier=3, topics=["x"])],
        2, None, None, "3 ítems, 2 duplicados → 2 resultantes",
    ),
]


@pytest.mark.parametrize("items,exp_len,exp_tier,exp_topics,_desc", DEDUPE_CASES)
def test_dedupe(items, exp_len, exp_tier, exp_topics, _desc):
    result = dedupe(items)
    assert len(result) == exp_len
    if exp_tier is not None:
        assert any(r.tier == exp_tier for r in result)
    if exp_topics is not None:
        assert any(r.topics == exp_topics for r in result)


# --- _score -----------------------------------------------------------------

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def test_score_tier_weight():
    """Tier 1 pesa más que tier 3 para la misma antigüedad."""
    assert _score(_it(tier=1, published=NOW)) > _score(_it(tier=3, published=NOW))


def test_score_no_date_penalty():
    """Ítem sin fecha obtiene menos score que uno reciente."""
    assert _score(_it(published=NOW)) > _score(_it(published=None))


def test_score_hn_bonus_applies():
    """Puntos HN positivos añaden bonus al score."""
    assert _score(_it(extra={"points": 300})) > _score(_it(extra={"points": 0}))


def test_score_hn_bonus_capped():
    """Bonus HN no excede 0.15, sin importar cuántos puntos."""
    assert _score(_it(extra={"points": 1500})) == _score(_it(extra={"points": 99999}))


def test_score_unknown_tier_defaults():
    """Tier no mapeado (ej. 99) usa peso por defecto 0.4, menor que tier 3 (0.45)."""
    assert _score(_it(tier=3, published=NOW)) > _score(_it(tier=99, published=NOW))


def test_score_older_lower():
    """A mayor antigüedad, menor score (mismo tier)."""
    recent = _it(published=NOW)
    older = _it(published=datetime(2026, 6, 19, 12, 0, tzinfo=timezone.utc))
    assert _score(recent) > _score(older)


# --- rank -------------------------------------------------------------------

def test_rank_empty():
    assert rank([]) == []


def test_rank_single():
    it = _it()
    assert rank([it]) == [it]


def test_rank_descending():
    """Tier 1 debe aparecer antes que tier 3 (misma fecha)."""
    low = _it(title="low", tier=3, published=NOW)
    high = _it(title="high", tier=1, published=NOW)
    result = rank([low, high])
    assert result[0].title == "high"
    assert result[1].title == "low"


# --- diversify --------------------------------------------------------------

def test_diversify_empty():
    assert diversify([]) == []


def test_diversify_single():
    result = diversify([_it()])
    assert len(result) == 1


def test_diversify_under_limit():
    """Todas las fuentes bajo el límite: sin overflow."""
    items = [
        _it(source_id="arxiv"),
        _it(source_id="arxiv", topics=["physics"]),
        _it(source_id="pubmed"),
    ]
    assert len(diversify(items)) == 3


def test_diversify_over_limit():
    """4 ítems misma fuente+tema → solo max en cabeza, resto al final."""
    items = [_it(source_id="arxiv") for _ in range(4)]
    result = diversify(items)
    assert len(result) == 4
    kept = result[:MAX_PER_SOURCE_TOPIC]
    over = result[MAX_PER_SOURCE_TOPIC:]
    assert all(it.source_id == "arxiv" for it in kept)
    assert all(it.source_id == "arxiv" for it in over)


def test_diversify_different_topics_separate_limits():
    """Misma fuente con distinto topic → cada topic tiene su propio límite."""
    items = [
        _it(source_id="arxiv", topics=["physics"]),
        _it(source_id="arxiv", topics=["ai"]),
        _it(source_id="arxiv", topics=["physics"]),
        _it(source_id="arxiv", topics=["ai"]),
    ]
    assert len(diversify(items)) == 4  # todos caben: 2 topics × 3 límite


def test_diversify_no_topics_uses_empty_key():
    """Ítems sin topics usan string vacío como clave de agrupación."""
    items = [
        _it(source_id="arxiv", topics=[]),
        _it(source_id="arxiv", topics=[]),
    ]
    assert len(diversify(items)) == 2


def test_diversify_preserves_order_within_groups():
    """Dentro de kept y overflow se preserva el orden original."""
    a = _it(title="A", source_id="s1")
    b = _it(title="B", source_id="s2")
    c = _it(title="C", source_id="s1")
    assert [it.title for it in diversify([a, b, c])] == ["A", "B", "C"]


def test_diversify_alternating_sources():
    """Varias fuentes alternadas: cada una suma a su propio contador."""
    items = [
        _it(source_id="s1"), _it(source_id="s2"),
        _it(source_id="s1"), _it(source_id="s2"),
        _it(source_id="s1"), _it(source_id="s2"),
        _it(source_id="s1"), _it(source_id="s2"),  # estos exceden el límite
    ]
    result = diversify(items)
    kept_sources = [it.source_id for it in result[:3*2]]
    assert kept_sources == ["s1", "s2", "s1", "s2", "s1", "s2"]
    assert len(result) == 8
