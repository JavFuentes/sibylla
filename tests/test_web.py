"""Tests para la generación de web estática.

Cubre _snippet (corte en palabra, elipsis), _fecha / _instante (formato de
fechas), _agrupar (orden y "otros" al final), y _assert_min_items.
"""

from datetime import datetime, timezone

import pytest

from sibylla.models import NewsItem
from sibylla.web import (
    STELLAR_NEWS_SCHEMA,
    _agrupar,
    _assert_min_items,
    _card_id,
    _fecha,
    _instante,
    _select_social,
    _snippet,
    _tarjeta,
    build_stellar_news_payload,
)

# --- helpers ---------------------------------------------------------------

FECHA = datetime(2026, 6, 21, tzinfo=timezone.utc)
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun",
            "jul", "ago", "sep", "oct", "nov", "dic"]
NO_DATE_ES = "s/f"
TOPIC_LABELS = {"ai": "Inteligencia artificial", "space": "Espacio",
                "medicine": "Medicina", "otros": "Otros"}


def _item(title="T", url="https://x.com", source_name="S", tier=2,
          topics=None, published=FECHA, summary="", image=None, source_id="test"):
    if topics is None:
        topics = ["ai"]
    return NewsItem(
        title=title, url=url, source_id=source_id, source_name=source_name,
        tier=tier, topics=topics, published=published,
        summary=summary, image=image,
    )


# ---------------------------------------------------------------------------
# _snippet
# ---------------------------------------------------------------------------
SNIPPET_CASES = [
    ("Hola", 220, "Hola", "texto corto sin cambios"),
    ("", 220, "", "cadena vacía"),
    ("   palabra   ", 220, "palabra", "whitespace extremo eliminado"),
    (
        "El " + "gato " * 50,
        30,
        "El gato gato gato gato gato…",
        "corte en frontera de palabra + elipsis",
    ),
    (
        # Justo en el límite: sin elipsis
        "a" * 220,
        220,
        "a" * 220,
        "exactamente en el límite",
    ),
    (
        # Corte en frontera de palabra; coma no es letra, no se elimina
        "primera parte, segunda parte con mas texto irrelevante",
        25,
        "primera parte, segunda…",
        "corte en frontera de palabra con límite pequeño",
    ),
    (
        # Palabra única muy larga sin espacios: truncado a secas
        "Pneumoultramicroscopicossilicovulcanoconiótico",
        20,
        "Pneumoultramicroscop…",
        "palabra única sin espacios, truncada",
    ),
]


@pytest.mark.parametrize("texto,limite,esperado,_desc", SNIPPET_CASES)
def test_snippet(texto, limite, esperado, _desc):
    assert _snippet(texto, limite) == esperado


def test_snippet_none_seguro():
    """None se trata como cadena vacía."""
    assert _snippet(None) == ""


# ---------------------------------------------------------------------------
# _fecha
# ---------------------------------------------------------------------------
FECHA_CASES = [
    (FECHA, "21 jun 2026", "fecha normal"),
    (datetime(2026, 1, 1, tzinfo=timezone.utc), "1 ene 2026", "primer día del año"),
    (datetime(2026, 12, 31, tzinfo=timezone.utc), "31 dic 2026", "último día del año"),
]


@pytest.mark.parametrize("dt,esperado,_desc", FECHA_CASES)
def test_fecha(dt, esperado, _desc):
    assert _fecha(dt, MESES_ES, NO_DATE_ES) == esperado


def test_fecha_none():
    """Sin fecha → etiqueta no_date."""
    assert _fecha(None, MESES_ES, NO_DATE_ES) == NO_DATE_ES


# ---------------------------------------------------------------------------
# _instante
# ---------------------------------------------------------------------------
def test_instante():
    dt = datetime(2026, 6, 21, 6, 54, tzinfo=timezone.utc)
    assert _instante(dt, MESES_ES) == "21 jun 2026, 06:54 UTC"


def test_instante_otro_mes():
    dt = datetime(2026, 3, 10, 14, 30, tzinfo=timezone.utc)
    assert _instante(dt, MESES_ES) == "10 mar 2026, 14:30 UTC"


# ---------------------------------------------------------------------------
# _agrupar
# ---------------------------------------------------------------------------
def test_agrupar_orden_segun_topics():
    """Los grupos aparecen en el orden de `topics`, no en el de los ítems."""
    items = [
        _item(title="B", topics=["space"]),
        _item(title="A", topics=["ai"]),
        _item(title="C", topics=["ai"]),
    ]
    grupos = _agrupar(items, topics=["ai", "space"], max_por_tema=6,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    assert [g["id"] for g in grupos] == ["ai", "space"]


def test_agrupar_otros_al_final():
    """Ítems sin topics van al grupo 'otros', al final."""
    items = [
        _item(title="A", topics=["ai"]),
        _item(title="Sin", topics=[]),
    ]
    grupos = _agrupar(items, topics=["ai"], max_por_tema=6,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    ids = [g["id"] for g in grupos]
    assert ids == ["ai", "otros"]


def test_agrupar_temas_no_listados_aparecen_al_final():
    """Un tema que no está en `topics` aparece después de los pedidos."""
    items = [
        _item(title="A", topics=["medicine"]),
        _item(title="B", topics=["ai"]),
    ]
    grupos = _agrupar(items, topics=["ai"], max_por_tema=6,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    ids = [g["id"] for g in grupos]
    assert ids == ["ai", "medicine"]


def test_agrupar_max_por_tema():
    """Respeta el límite de tarjetas por tema."""
    items = [_item(title=f"Item {i}", topics=["ai"]) for i in range(10)]
    grupos = _agrupar(items, topics=["ai"], max_por_tema=3,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    assert len(grupos[0]["cards"]) == 3


def test_agrupar_tema_sin_items_no_aparece():
    """Un tema en `topics` pero sin ítems no genera grupo."""
    items = [_item(topics=["ai"])]
    grupos = _agrupar(items, topics=["ai", "space"], max_por_tema=6,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    ids = [g["id"] for g in grupos]
    assert "space" not in ids


def test_agrupar_tema_duplicado_en_orden_no_se_repite():
    """Si el mismo tema aparece dos veces en la lista de orden, solo se emite una vez."""
    items = [_item(title="A", topics=["ai"])]
    grupos = _agrupar(items, topics=["ai", "ai"], max_por_tema=6,
                      topic_labels=TOPIC_LABELS, months=MESES_ES, no_date=NO_DATE_ES)
    assert len(grupos) == 1
    assert grupos[0]["id"] == "ai"


# ---------------------------------------------------------------------------
# _tarjeta (propagación de imagen)
# ---------------------------------------------------------------------------
def test_tarjeta_propaga_image():
    it = _item(image="https://cdn.example.com/x.jpg")
    card = _tarjeta(it, MESES_ES, NO_DATE_ES)
    assert card["image"] == "https://cdn.example.com/x.jpg"


def test_tarjeta_incluye_id_estable():
    it = _item(url="https://example.com/noticia")
    card = _tarjeta(it, MESES_ES, NO_DATE_ES)
    assert card["id"] == _card_id(it)
    assert card["id"].startswith("n-")


def test_tarjeta_image_placeholder_cuando_no_hay():
    it = _item()
    assert it.image is None
    card = _tarjeta(it, MESES_ES, NO_DATE_ES)
    assert card["image"] == "placeholder-test.png"


# ---------------------------------------------------------------------------
# _tarjeta (resumen y fallback de snippet)
# ---------------------------------------------------------------------------
def test_tarjeta_sin_resumen():
    it = _item()
    card = _tarjeta(it, MESES_ES, NO_DATE_ES)
    assert card["resumen"] is None
    assert card["has_resumen"] is False


def test_tarjeta_propaga_resumen():
    it = _item()
    card = _tarjeta(it, MESES_ES, NO_DATE_ES, resumenes={it.dedup_key: "Un resumen en ES."})
    assert card["resumen"] == "Un resumen en ES."
    assert card["has_resumen"] is True


def test_tarjeta_snippet_cae_al_resumen_si_no_hay_fuente():
    """Ítem sin summary y sin traducción: el snippet es un recorte del resumen."""
    it = _item(summary="")
    card = _tarjeta(it, MESES_ES, NO_DATE_ES, resumenes={it.dedup_key: "Resumen completo."})
    assert card["snippet"] == "Resumen completo."


def test_tarjeta_snippet_prefiere_fuente_sobre_resumen():
    """Si hay snippet de la fuente, no se pisa con el resumen."""
    it = _item(summary="Snippet de la fuente.")
    card = _tarjeta(it, MESES_ES, NO_DATE_ES, resumenes={it.dedup_key: "Resumen completo."})
    assert card["snippet"] == "Snippet de la fuente."


# ---------------------------------------------------------------------------
# _assert_min_items
# ---------------------------------------------------------------------------
def test_assert_min_items_levanta_cuando_pocos():
    items = [_item() for _ in range(2)]
    with pytest.raises(ValueError, match="al menos 5"):
        _assert_min_items(items, min_n=5)


def test_assert_min_items_no_levanta_cuando_suficientes():
    items = [_item() for _ in range(5)]
    _assert_min_items(items, min_n=5)  # no levanta


def test_assert_min_items_con_min_n_custom():
    items = [_item()]
    with pytest.raises(ValueError, match="al menos 3"):
        _assert_min_items(items, min_n=3)


# ---------------------------------------------------------------------------
# Contrato Stellar-View
# ---------------------------------------------------------------------------
def test_stellar_news_payload_prefiere_noticia_con_imagen():
    sin_imagen = _item(title="Sin imagen", url="https://example.com/a", image=None)
    con_imagen = _item(
        title="Con imagen",
        url="https://example.com/b",
        image="https://cdn.example.com/b.jpg",
        source_id="nasa",
        source_name="NASA",
        tier=1,
    )
    payload = build_stellar_news_payload(
        [sin_imagen, con_imagen],
        site_url="https://sibylla.cl",
        generated_at=FECHA,
        translate=False,
    )

    assert payload["schema"] == STELLAR_NEWS_SCHEMA
    assert payload["featured"]["original_url"] == "https://example.com/b"
    assert payload["featured"]["image_url"] == "https://cdn.example.com/b.jpg"
    assert payload["featured"]["has_real_image"] is True
    assert payload["featured"]["sibylla_url"].startswith("https://sibylla.cl/index.html#n-")


def test_stellar_news_payload_sin_imagen_publica_primera_y_marca_placeholder():
    primero = _item(title="Primero", url="https://example.com/a", image=None)
    segundo = _item(title="Segundo", url="https://example.com/b", image=None)
    payload = build_stellar_news_payload(
        [primero, segundo],
        site_url="https://sibylla.cl",
        generated_at=FECHA,
        translate=False,
    )

    assert payload["featured"]["original_url"] == "https://example.com/a"
    assert payload["featured"]["image_url"] is None
    assert payload["featured"]["has_real_image"] is False


def test_stellar_news_payload_expone_titulos_en_tres_idiomas():
    it = _item(title="Original title", url="https://example.com/a")
    payload = build_stellar_news_payload(
        [it],
        site_url="https://sibylla.cl",
        generated_at=FECHA,
        translations={it.dedup_key: {"title": "Titulo traducido"}},
        translate=False,
    )

    assert payload["featured"]["title"] == {
        "es": "Titulo traducido",
        "en": "Original title",
        "it": "Original title",
    }


# ---------------------------------------------------------------------------
# _select_social: Fase 2 (house cards por recencia + diversidad de red)
# ---------------------------------------------------------------------------
OLD = datetime(2026, 6, 20, tzinfo=timezone.utc)
MID = datetime(2026, 6, 22, tzinfo=timezone.utc)
NEW = datetime(2026, 6, 24, tzinfo=timezone.utc)


def _house(network, feed_ts, likes=0, reposts=0, title="H"):
    """House item mínimo para probar la selección (engagement irrelevante)."""
    return NewsItem(
        title=title, url=f"https://{network}.example/{title}",
        source_id=network, source_name=network, tier=3, published=feed_ts,
        extra={"kind": "post", "network": network, "house": True,
               "feed_ts": feed_ts, "likes": likes, "reposts": reposts},
    )


def test_select_social_house_dos_redes_distintas():
    """Con varias redes, las 2 house cards son de redes distintas (aunque la
    2ª de una red sea más nueva y con más engagement que la de otra red)."""
    house = [
        _house("mastodon", NEW, likes=0),
        _house("mastodon", MID, likes=999),   # 2º mastodon, alto engagement
        _house("bluesky", OLD, likes=0),       # bluesky más viejo, sin likes
    ]
    sel = _select_social([], house, {"shuffle": False}, "seed")
    assert {it.extra["network"] for it in sel} == {"mastodon", "bluesky"}


def test_select_social_house_misma_red_si_no_hay_otra():
    """Si solo una red aportó posts, las 2 tarjetas salen de esa misma red."""
    house = [
        _house("mastodon", NEW),
        _house("mastodon", MID),
        _house("mastodon", OLD),
    ]
    sel = _select_social([], house, {"shuffle": False}, "seed")
    assert [it.extra["network"] for it in sel] == ["mastodon", "mastodon"]


def test_select_social_house_recencia_ignora_engagement():
    """El post más reciente gana al más antiguo aunque este sea viral."""
    viejo_viral = _house("mastodon", OLD, likes=10000, reposts=10000, title="viral")
    nuevo_sin_likes = _house("mastodon", NEW, likes=0, reposts=0, title="nuevo")
    sel = _select_social([], [viejo_viral, nuevo_sin_likes], {"shuffle": False}, "seed")
    assert sel[0].title == "nuevo"
