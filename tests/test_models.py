"""Tests para el modelo de datos y utilidades de texto/URL.

Cubre:
  - canonicalize_url    (limpieza de tracking, www, https, trailing slash)
  - clean_text          (HTML, entidades, espacios)
  - normalize_title     (puntuación, minúsculas)
  - NewsItem            (__post_init__, dedup_key, age_hours)
"""
from datetime import datetime, timezone

import pytest

from sibylla.models import (
    NewsItem,
    canonicalize_url,
    clean_text,
    normalize_title,
    safe_link_url,
)

# ---------------------------------------------------------------------------
# canonicalize_url
# ---------------------------------------------------------------------------
CANONICAL_CASES = [
    # (entrada, esperado, descripción)
    ("", "", "cadena vacía"),
    (
        "https://example.com/article/",
        "https://example.com/article",
        "trailing slash eliminado",
    ),
    (
        "http://example.com",
        "https://example.com",
        "http -> https",
    ),
    (
        "https://www.example.com",
        "https://example.com",
        "www. eliminado",
    ),
    (
        "https://EXAMPLE.com/Path",
        "https://example.com/Path",
        "netloc a minúsculas",
    ),
    (
        "https://example.com?a=1&utm_source=x&b=2",
        "https://example.com?a=1&b=2",
        "tracking params eliminados, válidos conservados",
    ),
    (
        "https://example.com?gclid=123&fbclid=456",
        "https://example.com",
        "solo tracking params -> query vacía",
    ),
    (
        "https://example.com?Utm_Source=x",
        "https://example.com",
        "tracking param case-insensitive",
    ),
    (
        "https://example.com#section",
        "https://example.com",
        "fragment eliminado",
    ),
    (
        "ftp://files.example.com",
        "ftp://files.example.com",
        "esquema no-http preservado",
    ),
    (
        "not a url at all",
        "not a url at all",
        "URL malformada -> se devuelve limpia sin romper",
    ),
    (
        "https://example.com?a=1&ref=abc&ref_src=news&oc=1&smid=tw"
        "&spm=123&cmpid=456&mc_cid=789&mc_eid=012",
        "https://example.com?a=1",
        "múltiples tracking params del set completo",
    ),
    (
        "https://www.EXAMPLE.com/Path/?utm_medium=email",
        "https://example.com/Path",
        "combinado: www + trailing slash + mayúsculas + tracking",
    ),
]


@pytest.mark.parametrize("url,expected,_desc", CANONICAL_CASES)
def test_canonicalize_url(url, expected, _desc):
    assert canonicalize_url(url) == expected


def test_canonicalize_url_determinismo():
    """Dos URLs equivalentes (distinto tracking/www) producen la misma canónica."""
    a = "https://www.example.com/a?utm_source=x"
    b = "http://example.com/a?ref=y"
    assert canonicalize_url(a) == canonicalize_url(b) == "https://example.com/a"


# ---------------------------------------------------------------------------
# clean_text
# ---------------------------------------------------------------------------
CLEAN_CASES = [
    ("", "", "vacío"),
    ("<p>Hello <b>world</b></p>", "Hello world", "HTML tags eliminados"),
    ("DR &amp; CRISP R", "DR & CRISP R", "entidad HTML decodificada"),
    ("mucho   espacio", "mucho espacio", "espacios múltiples colapsados"),
    ("  leading trailing  ", "leading trailing", "espacios extremos eliminados"),
    ("<div>\n  línea\n</div>", "línea", "salto de línea colapsado a espacio"),
    ("sin cambios", "sin cambios", "texto limpio sin alterar"),
    ("&lt;tag&gt;", "<tag>", "entidades de ángulos"),
]


@pytest.mark.parametrize("text,expected,_desc", CLEAN_CASES)
def test_clean_text(text, expected, _desc):
    assert clean_text(text) == expected


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------
NORMALIZE_CASES = [
    ("", "", "vacío"),
    ("Hello, World! AI", "hello world ai", "puntuación eliminada, lowercase"),
        ("¿Qué es la IA?", "qué es la ia", "signos de interrogación y tilde (é se preserva)"),
    (
        "DR &amp; CRISP R",
        "dr crisp r",
        "entidad HTML + puntuación & eliminada",
    ),
]


@pytest.mark.parametrize("title,expected,_desc", NORMALIZE_CASES)
def test_normalize_title(title, expected, _desc):
    assert normalize_title(title) == expected


# ---------------------------------------------------------------------------
# safe_link_url
# ---------------------------------------------------------------------------
SAFE_LINK_CASES = [
    ("", "", "vacío"),
    ("https://example.com/a", "https://example.com/a", "https preservado"),
    ("http://example.com/a", "http://example.com/a", "http preservado"),
    ("javascript:alert(1)", "", "esquema javascript: bloqueado"),
    ("JavaScript:alert(1)", "", "esquema javascript: case-insensitive"),
    ("data:text/html,<script>alert(1)</script>", "", "esquema data: bloqueado"),
    ("vbscript:msgbox(1)", "", "esquema vbscript: bloqueado"),
    ("  https://example.com/a  ", "https://example.com/a", "espacios recortados"),
    ("ftp://files.example.com", "", "esquema ftp no permitido en enlaces"),
    ("not a url at all", "", "sin esquema -> bloqueado"),
]


@pytest.mark.parametrize("url,expected,_desc", SAFE_LINK_CASES)
def test_safe_link_url(url, expected, _desc):
    assert safe_link_url(url) == expected


# ---------------------------------------------------------------------------
# NewsItem.__post_init__
# ---------------------------------------------------------------------------
def test_post_init_cleans_title_and_summary():
    it = NewsItem(
        title="<b>Hola</b>",
        url="https://x.com",
        source_id="test",
        source_name="Test",
        tier=2,
        summary="  resumen &amp; más  ",
    )
    assert it.title == "Hola"
    assert it.summary == "resumen & más"


def test_post_init_naive_datetime_becomes_utc():
    naive = datetime(2026, 1, 1)
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        published=naive,
    )
    assert it.published.tzinfo == timezone.utc
    assert it.published.year == 2026


def test_post_init_aware_datetime_preserved():
    aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        published=aware,
    )
    assert it.published is aware


def test_post_init_published_none():
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        published=None,
    )
    assert it.published is None


def test_post_init_sanea_url_con_esquema_peligroso():
    """Un feed comprometido (p. ej. post federado de Mastodon) no puede colar
    una URL javascript: en el href de la tarjeta."""
    it = NewsItem(
        title="t", url="javascript:alert(document.cookie)",
        source_id="s", source_name="S", tier=1,
    )
    assert it.url == ""


# ---------------------------------------------------------------------------
# NewsItem.dedup_key
# ---------------------------------------------------------------------------
def test_dedup_key_with_url():
    it = NewsItem(
        title="Some Title!",
        url="https://www.example.com/article/?utm_source=x",
        source_id="s", source_name="S", tier=1,
    )
    assert it.dedup_key == "u:https://example.com/article"


def test_dedup_key_without_url():
    it = NewsItem(
        title="Some Title!",
        url="",
        source_id="s", source_name="S", tier=1,
    )
    assert it.dedup_key == "t:some title"


def test_dedup_key_deduplica_por_url():
    a = NewsItem(
        title="A", url="https://www.example.com/x?utm_source=1",
        source_id="s1", source_name="S1", tier=1,
    )
    b = NewsItem(
        title="B", url="http://example.com/x?ref=2",
        source_id="s2", source_name="S2", tier=2,
    )
    assert a.dedup_key == b.dedup_key


def test_dedup_key_deduplica_por_titulo_cuando_no_hay_url():
    a = NewsItem(
        title="Hello, World!", url="",
        source_id="s1", source_name="S1", tier=1,
    )
    b = NewsItem(
        title="hello world", url="",
        source_id="s2", source_name="S2", tier=2,
    )
    assert a.dedup_key == b.dedup_key


# ---------------------------------------------------------------------------
# NewsItem.age_hours
# ---------------------------------------------------------------------------
def test_age_hours_con_fecha():
    now = datetime.now(timezone.utc)
    publicado = datetime(2020, 1, 1, tzinfo=timezone.utc)
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        published=publicado,
    )
    # publicado en 2020: edad enorme pero finita
    assert it.age_hours > 10000


def test_age_hours_sin_fecha():
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        published=None,
    )
    assert it.age_hours == 1e9


# ---------------------------------------------------------------------------
# Propiedad canonical_url (atajo en NewsItem)
# ---------------------------------------------------------------------------
def test_canonical_url_property():
    it = NewsItem(
        title="t", url="https://www.EXAMPLE.com/a/?ref=x",
        source_id="s", source_name="S", tier=1,
    )
    assert it.canonical_url == "https://example.com/a"


# ---------------------------------------------------------------------------
# NewsItem.image
# ---------------------------------------------------------------------------
def test_image_default_none():
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
    )
    assert it.image is None


def test_image_se_guarda():
    it = NewsItem(
        title="t", url="https://x.com", source_id="s", source_name="S", tier=1,
        image="https://cdn.example.com/img.jpg",
    )
    assert it.image == "https://cdn.example.com/img.jpg"
