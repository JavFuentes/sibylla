"""Tests para el filtro de relevancia bilingüe (ES/EN, sin tildes).

Cubre:
  - _strip_accents     (NFD, categoría Mn)
  - is_relevant        (keyword corta con word-boundary, larga con substring,
                         case-insensitive, acentos, tema desconocido)
  - classify_topics    (combinación título+summary, múltiples temas)
"""
import pytest

from sibylla.fetchers import _strip_accents, classify_topics, is_relevant

# ---------------------------------------------------------------------------
# _strip_accents
# ---------------------------------------------------------------------------
STRIP_ACCENTS_CASES = [
    ("inteligencia", "inteligencia", "sin tildes -> sin cambios"),
    ("inteligéncia", "inteligencia", "tilde aguda eliminada"),
    ("año", "ano", "ñ sin tilde, tilde en n eliminada"),
    ("", "", "vacío"),
    ("artículo científico", "articulo cientifico", "varias tildes"),
    ("MÉXICO", "MEXICO", "mayúsculas con tilde"),
]


@pytest.mark.parametrize("text,expected,_desc", STRIP_ACCENTS_CASES)
def test_strip_accents(text, expected, _desc):
    assert _strip_accents(text) == expected


# ---------------------------------------------------------------------------
# is_relevant — keyword corta (≤3 chars, word boundary)
# ---------------------------------------------------------------------------
SHORT_KW_CASES = [
    # (título, tema, esperado, descripción)
    ("AI breakthrough in medicine", "ai", True, "ai como palabra independiente"),
    ("Airport security upgrade", "ai", False, "airport NO es ai (boundary)"),
    ("The AI revolution", "ai", True, "AI tras artículo"),
    ("mainframe ai", "ai", True, "ai al final de frase"),
    ("ESA launches telescope", "space", True, "esa keyword acrónimo"),
    ("mesa redonda de ciencia", "space", False, "esa dentro de mesa -> no match"),
    ("GPT-5 announced", "ai", True, "gpt con guión (boundary)"),
    ("LLM evaluation benchmark", "ai", True, "llm keyword corta (3 chars)"),
    ("fusión nuclear avanza", "physics", True, "otra keyword de 3: no aplica"),
]


@pytest.mark.parametrize("title,topic,expected,_desc", SHORT_KW_CASES)
def test_is_relevant_short_keyword(title, topic, expected, _desc):
    assert is_relevant(title, topic) == expected


# ---------------------------------------------------------------------------
# is_relevant — keyword larga (>3 chars, substring)
# ---------------------------------------------------------------------------
LONG_KW_CASES = [
    ("generative AI changes industry", "ai", True, "generativ ⊆ generative"),
    ("generación de texto", "ai", False, "generativ ⊄ generacion"),
    ("quantum computing advance", "computing", True, "quantum comput match"),
    (
        "nuevo método de aprendizaje automático",
        "ai",
        True,
        "aprendizaje automatico (tilde stripped) match",
    ),
    ("inteligencia artificial avanza", "ai", True, "frase bilingüe completa"),
    ("ARTIFICIAL INTELLIGENCE", "ai", True, "mayúsculas -> lowercase"),
    ("machine learning new approach", "ai", True, "machine learning keyword"),
    (
        "artículo científico revela hallazgo",
        "general_science",
        True,
        "cientific (tilde stripped) match",
    ),
    ("drug trial shows promise", "medicine", True, "drug keyword match"),
    ("célula madre reprogramada", "biotech", True, "celula madre (tilde stripped)"),
]


@pytest.mark.parametrize("title,topic,expected,_desc", LONG_KW_CASES)
def test_is_relevant_long_keyword(title, topic, expected, _desc):
    assert is_relevant(title, topic) == expected


# ---------------------------------------------------------------------------
# is_relevant — edge cases
# ---------------------------------------------------------------------------
def test_is_relevant_empty_title():
    assert is_relevant("", "ai") is False


def test_is_relevant_unknown_topic():
    """Tema sin keywords -> todo pasa (no hay filtro)."""
    assert is_relevant("cualquier cosa irrelevante", "tema_inexistente") is True


def test_is_relevant_first_keyword_wins():
    """Si el primer keyword matchea, no se prueban los demás (early return)."""
    # "ai" (corto) aparece al principio -> match inmediato
    assert is_relevant("ai revolution and artificial intelligence", "ai") is True


# ---------------------------------------------------------------------------
# classify_topics
# ---------------------------------------------------------------------------
def test_classify_topics_multiple_matches():
    """Título+summary relevantes a varios temas -> devuelve todos."""
    result = classify_topics(
        title="AI drug discovery",
        summary="ML techniques for pharma research",
        topics=["ai", "medicine", "space"],
    )
    assert sorted(result) == ["ai", "medicine"]


def test_classify_topics_no_match():
    """Texto irrelevante -> lista vacía."""
    result = classify_topics(
        title="football match results",
        summary="champions league final score",
        topics=["ai", "medicine"],
    )
    assert result == []


def test_classify_topics_single_match():
    result = classify_topics(
        title="NASA finds water on Mars",
        summary="",
        topics=["ai", "space"],
    )
    assert result == ["space"]


def test_classify_topics_respeta_solo_topics_pedidos():
    """Aunque el texto contenga keywords de 'space', si no se pidió, no se
    devuelve."""
    result = classify_topics(
        title="NASA finds water on Mars",
        summary="",
        topics=["general_science"],
    )
    # "nasa" es keyword de space, no de general_science. "mars" tampoco.
    # "water" no es keyword de general_science.
    assert "space" not in result
