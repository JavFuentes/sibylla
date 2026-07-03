"""Tests de las publicaciones propias de Sibylla (sección SIBYLLA).

Cubre `_parse_publicacion` (front-matter YAML + cuerpo, casos inválidos y
borradores) y `seleccionar_publicaciones` (filtro de fechas futuras, orden
descendente y tope de tarjetas). Todo puro: sin disco ni red.
"""

from datetime import datetime, timezone

import pytest

from sibylla.publicaciones import (
    SIBYLLA_MAX_TOTAL,
    SIBYLLA_SOURCE_ID,
    _parse_publicacion,
    seleccionar_publicaciones,
)

AHORA = datetime(2026, 7, 3, 12, 0, tzinfo=timezone.utc)

VALIDA = """---
titulo: "Sibylla estrena su propia voz"
fecha: 2026-07-03
resumen: "Una bajada corta."
---
Primer párrafo del cuerpo.

Segundo párrafo.
"""


def _pub(titulo="T", fecha=AHORA):
    it = _parse_publicacion(f'---\ntitulo: "{titulo}"\nfecha: 2026-01-01\n---\n')
    it.published = fecha
    return it


# ---------------------------------------------------------------------------
# _parse_publicacion — casos válidos
# ---------------------------------------------------------------------------
def test_parse_titulo():
    assert _parse_publicacion(VALIDA).title == "Sibylla estrena su propia voz"


def test_parse_fecha_utc_aware():
    assert _parse_publicacion(VALIDA).published == datetime(2026, 7, 3, tzinfo=timezone.utc)


def test_parse_resumen_como_summary():
    assert _parse_publicacion(VALIDA).summary == "Una bajada corta."


def test_parse_cuerpo_en_extra_body():
    assert _parse_publicacion(VALIDA).extra["body"] == "Primer párrafo del cuerpo.\n\nSegundo párrafo."


def test_parse_source_id():
    assert _parse_publicacion(VALIDA).source_id == SIBYLLA_SOURCE_ID


def test_parse_tier_primaria():
    assert _parse_publicacion(VALIDA).tier == 1


def test_parse_network_para_pill():
    assert _parse_publicacion(VALIDA).extra["network"] == SIBYLLA_SOURCE_ID


def test_parse_slug_desde_parametro():
    # El slug (stem del archivo) se guarda en extra para generar pub/<slug>.html.
    assert _parse_publicacion(VALIDA, slug="2026-07-03-mi-noticia").extra["slug"] \
        == "2026-07-03-mi-noticia"


def test_parse_sin_slug_queda_none():
    assert _parse_publicacion(VALIDA).extra["slug"] is None


def test_parse_sin_url_queda_vacia():
    assert _parse_publicacion(VALIDA).url == ""


def test_parse_url_externa():
    texto = '---\ntitulo: "T"\nfecha: 2026-07-01\nurl: https://ejemplo.cl/nota\n---\n'
    assert _parse_publicacion(texto).url == "https://ejemplo.cl/nota"


def test_parse_url_esquema_peligroso_filtrada():
    # safe_link_url (models.py) descarta esquemas no http(s).
    texto = '---\ntitulo: "T"\nfecha: 2026-07-01\nurl: "javascript:alert(1)"\n---\n'
    assert _parse_publicacion(texto).url == ""


def test_parse_fecha_con_hora():
    texto = '---\ntitulo: "T"\nfecha: 2026-07-01 09:30\n---\n'
    assert _parse_publicacion(texto).published == datetime(2026, 7, 1, 9, 30, tzinfo=timezone.utc)


def test_parse_sin_cuerpo_body_vacio():
    texto = '---\ntitulo: "T"\nfecha: 2026-07-01\n---\n'
    assert _parse_publicacion(texto).extra["body"] == ""


# ---------------------------------------------------------------------------
# _parse_publicacion — casos que NO publican (None)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("texto,_desc", [
    ("", "cadena vacía"),
    ("Solo texto sin front-matter.", "sin delimitadores ---"),
    ('---\ntitulo: "T"\nfecha: 2026-07-01\npublicado: false\n---\n', "borrador explícito"),
    ('---\nfecha: 2026-07-01\n---\n', "sin titulo"),
    ('---\ntitulo: "T"\n---\n', "sin fecha"),
    ('---\ntitulo: "T"\nfecha: "ayer"\n---\n', "fecha no ISO"),
    ('---\n- solo\n- una\n- lista\n---\n', "front-matter que no es un mapa"),
    ('---\ntitulo: "T\nfecha: [rota\n---\n', "YAML ilegible"),
])
def test_parse_invalida_devuelve_none(texto, _desc):
    assert _parse_publicacion(texto, "caso.md") is None


# ---------------------------------------------------------------------------
# seleccionar_publicaciones
# ---------------------------------------------------------------------------
def test_seleccion_descarta_futuras():
    futura = _pub("Futura", AHORA.replace(year=2027))
    vigente = _pub("Vigente", AHORA.replace(day=1))
    assert seleccionar_publicaciones([futura, vigente], now=AHORA) == [vigente]


def test_seleccion_orden_descendente_por_fecha():
    vieja = _pub("Vieja", AHORA.replace(month=1))
    nueva = _pub("Nueva", AHORA.replace(day=2))
    assert seleccionar_publicaciones([vieja, nueva], now=AHORA) == [nueva, vieja]


def test_seleccion_corta_al_tope():
    items = [_pub(f"P{i}", AHORA.replace(month=1, day=i + 1))
             for i in range(SIBYLLA_MAX_TOTAL + 2)]
    assert len(seleccionar_publicaciones(items, now=AHORA)) == SIBYLLA_MAX_TOTAL


def test_seleccion_vacia_sin_items():
    assert seleccionar_publicaciones([], now=AHORA) == []
