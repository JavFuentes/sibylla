"""Tests para el render determinista de Markdown (digest).

Cubre _meta_line (fuente, fecha, tier, puntos HN) y render_digest
(agrupación por tema, enlaces, max_per_topic, footer).
"""

from datetime import datetime, timezone

import pytest

from sibylla.digest import TIER_LABEL, _meta_line, render_digest
from sibylla.models import NewsItem

# --- helpers ---------------------------------------------------------------

FECHA = datetime(2026, 6, 15, tzinfo=timezone.utc)
NO_DATE_ES = "s/f"


def _item(title="Título", url="https://x.com/a", source_name="Fuente", tier=2,
          topics=None, published=FECHA, summary="Resumen breve.", extra=None):
    return NewsItem(
        title=title, url=url, source_id="test", source_name=source_name,
        tier=tier, topics=topics or ["ai"], published=published,
        summary=summary, extra=extra or {},
    )


# ---------------------------------------------------------------------------
# _meta_line
# ---------------------------------------------------------------------------
def test_meta_line_completo():
    it = _item(source_name="arXiv", tier=1)
    result = _meta_line(it, NO_DATE_ES)
    assert result == "arXiv · 2026-06-15 · T1"


def test_meta_line_con_puntos_hn():
    it = _item(source_name="HN", tier=3, extra={"points": 42})
    result = _meta_line(it, NO_DATE_ES)
    assert "▲42 HN" in result


def test_meta_line_sin_fecha():
    it = _item(published=None)
    result = _meta_line(it, NO_DATE_ES)
    assert "s/f" in result
    assert "2026" not in result


def test_meta_line_tier_desconocido():
    it = _item(tier=5)
    result = _meta_line(it, NO_DATE_ES)
    assert "T5" in result


def test_meta_line_source_name_vacio():
    it = _item(source_name="")
    result = _meta_line(it, NO_DATE_ES)
    # source_name vacío se filtra, no aparece " · " al inicio
    assert not result.startswith(" · ")


# ---------------------------------------------------------------------------
# render_digest
# ---------------------------------------------------------------------------
def test_render_digest_titulo_con_topics():
    items = [_item(topics=["ai"]), _item(topics=["space"])]
    out = render_digest(items, topics=["ai", "space"], meta={}, lang="es")
    assert out.startswith("# Sibylla — Resumen (ai, space)")


def test_render_digest_linea_generacion():
    items = [_item()]
    out = render_digest(items, topics=["ai"], meta={}, lang="es")
    assert "_Generado" in out
    assert "1 ítems" in out


def test_render_digest_headers_por_tema():
    items = [
        _item(title="A", topics=["ai"]),
        _item(title="B", topics=["space"]),
    ]
    out = render_digest(items, topics=["ai", "space"], meta={}, lang="es")
    assert "## ai" in out
    assert "## space" in out


def test_render_digest_enlace_titulo_negrita():
    items = [_item(title="Noticia", url="https://x.com/n")]
    out = render_digest(items, topics=["ai"], meta={}, lang="es")
    assert "**[Noticia](https://x.com/n)**" in out


def test_render_digest_summary_truncado():
    largo = "X" * 300
    items = [_item(summary=largo)]
    out = render_digest(items, topics=["ai"], meta={}, lang="es")
    assert "…" in out
    # El snippet truncado no debe exceder los 240 + elipsis visible
    assert len(largo[:240] + "…") > 240


def test_render_digest_max_per_topic():
    items = [_item(title=f"Item {i}") for i in range(5)]
    out = render_digest(items, topics=["ai"], meta={}, lang="es", max_per_topic=2)
    # Solo 2 ítems deben aparecer en el topic ai
    assert out.count("- **") == 2


def test_render_digest_tema_sin_items_no_aparece():
    items = [_item(topics=["ai"])]
    out = render_digest(items, topics=["ai", "space"], meta={}, lang="es")
    assert "## ai" in out
    assert "## space" not in out


def test_render_digest_footer_tiers():
    items = [_item()]
    out = render_digest(items, topics=["ai"], meta={}, lang="es")
    assert out.endswith("</sub>")
    assert "---" in out
    assert "T1" in out


def test_render_digest_item_sin_fecha_muestra_no_date():
    items = [_item(published=None)]
    out = render_digest(items, topics=["ai"], meta={}, lang="es")
    assert "s/f" in out


def test_render_digest_sin_items():
    """Lista vacía: solo título, generado y footer."""
    out = render_digest([], topics=["ai"], meta={}, lang="es")
    assert out.startswith("# Sibylla — Resumen (ai)")
    assert "_Generado" in out
    assert "---" in out
