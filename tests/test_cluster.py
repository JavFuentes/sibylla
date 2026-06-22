"""Tests para la agrupación de "misma noticia" entre medios (sibylla/cluster.py).

Cubre:
  - _tokens         (tokenización: stopwords ES/EN, tildes, palabras cortas)
  - _similar        (Jaccard + mínimo de tokens compartidos)
  - cluster_stories (fusión por similitud, representante por tier, related,
                     guardas por fuente y por ventana de fecha, unión de temas)

Tests puros, sin red (ver TEST.md).
"""
from datetime import datetime, timezone

import pytest

from sibylla.cluster import _entities, _similar, _tokens, cluster_stories
from sibylla.models import NewsItem

NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _it(title="T", url=None, source_id="s", source_name="S",
        tier=2, topics=None, published=NOW):
    return NewsItem(
        title=title, url=url or f"https://{source_id}.com/{abs(hash(title)) % 999}",
        source_id=source_id, source_name=source_name,
        tier=tier, topics=topics or [], published=published,
    )


# --- _tokens ----------------------------------------------------------------

TOKEN_CASES = [
    ("AI model discovers new antibiotic", {"model", "discovers", "antibiotic"},
     "quita stopwords EN (new) y tokens cortos (ai)"),
    ("El nuevo estudio sobre la galaxia", {"galaxia"},
     "quita stopwords ES (el, nuevo, estudio, sobre, la)"),
    ("Telescopio observa galaxía lejana", {"telescopio", "observa", "galaxia", "lejana"},
     "quita tildes: galaxía → galaxia"),
    ("", set(), "título vacío → sin tokens"),
    ("the and for with", set(), "solo stopwords → sin tokens"),
]


@pytest.mark.parametrize("title,expected,_desc", TOKEN_CASES)
def test_tokens(title, expected, _desc):
    assert _tokens(title) == expected


# --- _similar ---------------------------------------------------------------

SIMILAR_CASES = [
    ({"a", "b", "c"}, {"a", "b", "c"}, True, "conjuntos idénticos → Jaccard 1.0"),
    ({"a", "b", "c", "d"}, {"a", "b", "c"}, True, "Jaccard 3/4 = 0.75 ≥ 0.5"),
    ({"a", "b", "c", "d"}, {"a", "b"}, True, "Jaccard 2/4 = 0.5: el umbral es ≥, cumple"),
    ({"a", "b"}, {"a", "x"}, False, "1 token compartido < MIN_SHARED"),
    ({"a", "b", "c"}, {"x", "y", "z"}, False, "sin solape"),
    ({"a", "b", "c", "d", "e"}, {"a", "b"}, False, "Jaccard 2/5 = 0.4 < 0.5"),
    (set(), {"a", "b"}, False, "conjunto vacío nunca casa"),
]


@pytest.mark.parametrize("a,b,expected,_desc", SIMILAR_CASES)
def test_similar(a, b, expected, _desc):
    assert _similar(frozenset(a), frozenset(b), threshold=0.5, min_shared=2) is expected


# --- cluster_stories: bordes ------------------------------------------------

def test_cluster_empty():
    assert cluster_stories([]) == []


def test_cluster_single_no_related():
    [rep] = cluster_stories([_it(title="Solo una noticia importante")])
    assert rep.related == []


def test_cluster_untitled_stay_separate():
    """Ítems sin título (sin tokens) nunca se agrupan."""
    items = [_it(title="", source_id="a"), _it(title="", source_id="b")]
    assert len(cluster_stories(items)) == 2


# --- cluster_stories: fusión ------------------------------------------------

def test_cluster_same_story_merges():
    """Misma historia en dos medios distintos → 1 representante + 1 related."""
    items = [
        _it(title="AI model discovers powerful new antibiotic", source_id="nature", source_name="Nature"),
        _it(title="AI model discovers powerful antibiotic", source_id="bbc", source_name="BBC"),
    ]
    result = cluster_stories(items)
    assert len(result) == 1
    assert len(result[0].related) == 1


def test_cluster_distinct_stories_not_merged():
    """Historias sin tokens en común → no se fusionan."""
    items = [
        _it(title="Webb telescope captures distant galaxy", source_id="nasa"),
        _it(title="AI model discovers powerful antibiotic", source_id="nature"),
    ]
    assert len(cluster_stories(items)) == 2


def test_cluster_single_shared_token_not_merged():
    """Un solo token en común no basta (MIN_SHARED=2)."""
    items = [
        _it(title="Quantum computer breakthrough announced", source_id="ieee"),
        _it(title="Quantum entanglement lecture series", source_id="bbc"),
    ]
    assert len(cluster_stories(items)) == 2


def test_cluster_same_source_not_merged():
    """Dos ítems del MISMO medio con títulos similares = historias distintas."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="arxiv", url="https://arxiv.org/1"),
        _it(title="AI model discovers powerful antibiotic", source_id="arxiv", url="https://arxiv.org/2"),
    ]
    assert len(cluster_stories(items)) == 2


def test_cluster_representative_is_lowest_tier():
    """El representante es la fuente más fiable (menor tier)."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="bbc", source_name="BBC", tier=2),
        _it(title="AI model discovers powerful new antibiotic", source_id="nature", source_name="Nature", tier=1),
    ]
    [rep] = cluster_stories(items)
    assert rep.source_name == "Nature"


def test_cluster_related_has_other_source():
    """El medio no representante queda en `related` con sus campos."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="nature", source_name="Nature", tier=1),
        _it(title="AI model discovers powerful new antibiotic", source_id="bbc", source_name="BBC", tier=2),
    ]
    [rep] = cluster_stories(items)
    assert rep.related[0]["source_name"] == "BBC"
    assert "url" in rep.related[0] and rep.related[0]["tier"] == 2


def test_cluster_merges_topics():
    """El representante une los temas de los satélites (orden: rep primero)."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="nature", topics=["ai"], tier=2),
        _it(title="AI model discovers powerful new antibiotic", source_id="bbc", topics=["medicine"], tier=2),
    ]
    [rep] = cluster_stories(items)
    assert rep.topics == ["ai", "medicine"]


def test_cluster_related_sorted_by_tier():
    """`related` se ordena por tier (más fiable primero)."""
    base = "AI model discovers powerful antibiotic"
    items = [
        _it(title=base, source_id="nature", tier=1),          # representante
        _it(title=base, source_id="aggreg", tier=3),
        _it(title=base, source_id="journal", tier=2),
        _it(title=base, source_id="blog", tier=4),
    ]
    [rep] = cluster_stories(items)
    assert [r["tier"] for r in rep.related] == [2, 3, 4]


def test_cluster_date_window_blocks_far_apart():
    """Misma historia pero con > 14 días de diferencia → no se fusiona."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="nature",
            published=datetime(2026, 6, 1, tzinfo=timezone.utc)),
        _it(title="AI model discovers powerful antibiotic", source_id="bbc",
            published=datetime(2026, 6, 21, tzinfo=timezone.utc)),
    ]
    assert len(cluster_stories(items)) == 2


def test_cluster_date_window_allows_close():
    """Misma historia dentro de la ventana de fechas → se fusiona."""
    items = [
        _it(title="AI model discovers powerful antibiotic", source_id="nature",
            published=datetime(2026, 6, 18, tzinfo=timezone.utc)),
        _it(title="AI model discovers powerful antibiotic", source_id="bbc",
            published=datetime(2026, 6, 21, tzinfo=timezone.utc)),
    ]
    assert len(cluster_stories(items)) == 1


def test_cluster_spanish_titles_merge():
    """La tokenización ES (stopwords/tildes) permite agrupar titulares en español."""
    items = [
        _it(title="El nuevo telescopio observa una galaxia lejana", source_id="sinc", tier=2),
        _it(title="Telescopio observa galaxia lejana según científicos", source_id="agencia", tier=2),
    ]
    assert len(cluster_stories(items)) == 1


# --- _entities (señal de corroboración para Nacional) -----------------------

ENTITY_CASES = [
    ("Boric anuncia reforma a Codelco", {"boric", "codelco"},
     "nombre propio inicial + entidad capitalizada"),
    ("SQM y Codelco firman acuerdo de litio", {"sqm", "codelco"},
     "acrónimo en mayúsculas + entidad"),
    ("Gobierno Anuncia Nueva Reforma Tributaria Total", set(),
     "Title Case (casi todo capitalizado) -> sin entidades"),
    ("la crisis del agua en el norte", set(),
     "sin mayúsculas -> sin entidades"),
    ("Renunció Jackson", set(),
     "título muy corto (<3 palabras) -> sin señal"),
]


@pytest.mark.parametrize("title,expected,_desc", ENTITY_CASES)
def test_entities(title, expected, _desc):
    assert _entities(title) == frozenset(expected)


# --- cluster_stories: fusión por entidades compartidas ----------------------

def test_cluster_merges_by_shared_entities():
    """Dos medios distintos con el titular reescrito pero que comparten 2 nombres
    propios (Contraloría, Codelco) -> una sola historia, aunque el Jaccard de
    tokens no alcance el umbral. Es la señal de corroboración de Nacional."""
    items = [
        _it(title="Contraloría investiga a Codelco por contratos", source_id="ciper"),
        _it(title="Codelco responde a la Contraloría tras denuncia", source_id="interferencia"),
    ]
    assert len(cluster_stories(items)) == 1


def test_cluster_no_merge_with_single_shared_entity():
    """Una sola entidad en común y Jaccard bajo -> NO fusiona (conservador)."""
    items = [
        _it(title="Codelco anuncia plan de inversión minera", source_id="ciper"),
        _it(title="El litio domina la agenda según expertos", source_id="interferencia"),
    ]
    assert len(cluster_stories(items)) == 2
