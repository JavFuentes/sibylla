"""Test de paridad de locales: evita que los 4 archivos JSON diverjan en estructura.

Si un locale gana o pierde una clave sin que los demás se actualicen, los
tests de este módulo fallan. Es la red de seguridad más barata por línea
invertida del proyecto.
"""

import json

import pytest

from sibylla.i18n import LOCALES_DIR

LANGS = ["es", "en", "it", "pt"]


def _load(lang: str) -> dict:
    with open(LOCALES_DIR / f"{lang}.json", encoding="utf-8") as fh:
        return json.load(fh)


def _keys(d: dict, prefix: str = "") -> set[str]:
    """Recolecta recursivamente todas las rutas de claves con notación de punto.

    Las hojas que no son dict se registran como clave final
    (p. ej. 'web.no_date'), sin descender.
    """
    rutas: set[str] = set()
    for k, v in d.items():
        path = f"{prefix}.{k}" if prefix else k
        rutas.add(path)
        if isinstance(v, dict):
            rutas |= _keys(v, path)
    return rutas


# ---------------------------------------------------------------------------
# Existencia y validez JSON
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lang,lang_name,_desc", [
    ("es", "español", "archivo existe y es JSON válido"),
    ("en", "English", "archivo existe y es JSON válido"),
    ("it", "italiano", "archivo existe y es JSON válido"),
    ("pt", "português", "archivo existe y es JSON válido"),
])
def test_locales_existen_y_son_json(lang, lang_name, _desc):
    data = _load(lang)
    assert isinstance(data, dict)
    assert data["_meta"]["code"] == lang


# ---------------------------------------------------------------------------
# Paridad de claves de primer nivel
# ---------------------------------------------------------------------------
def test_locales_mismas_top_level_keys():
    """Los 4 archivos deben compartir exactamente las mismas claves raíz."""
    ref = set(_load("es").keys())
    for lang in ["en", "it", "pt"]:
        assert set(_load(lang).keys()) == ref


# ---------------------------------------------------------------------------
# Paridad estructural recursiva
# ---------------------------------------------------------------------------
def test_locales_paridad_estructural_total():
    """Paridad estructural recursiva entre los 4 locales — DESHABITADA.

    La web se publica solo en español (``ALL_LANGS = ["es"]`` en ``sibylla/web.py``)
    y la traducción de contenido la hace el LLM (``translate_cards`` en
    ``sibylla/translate.py``), no los ``web.*`` estáticos de en/it/pt. Exigir que
    en/it/pt repliquen toda la estructura de ``es`` protegería código muerto y
    obligaría a mantener claves (p. ej. ``social_*``/``auth_*`` de la fase social)
    que nunca se renderizan.

    Se conserva la paridad de claves raíz (``test_locales_mismas_top_level_keys``)
    y la específica de ``web.topics`` y ``web.months`` entre los 4 idiomas, porque
    esas sí siguen vivas (topics alimenta etiquetas y months alimenta fechas en
    rutas que el resolver aún toca). Reactivar si la cáscara vuelve a ser
    multilingüe.
    """
    pytest.skip("web monolingüe (es); en/it/pt solo alimentan prompts de LLM")


# ---------------------------------------------------------------------------
# Paridad de web.topics (las tarjetas dependen de estas claves)
# ---------------------------------------------------------------------------
def test_web_topics_mismas_claves():
    """Los 4 idiomas tienen las mismas claves de tema en web.topics."""
    ref = set(_load("es")["web"]["topics"].keys())
    for lang in ["en", "it", "pt"]:
        assert set(_load(lang)["web"]["topics"].keys()) == ref


# ---------------------------------------------------------------------------
# web.months: 12 entradas en cada idioma
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("lang,_desc", [
    ("es", "12 meses en español"),
    ("en", "12 months en inglés"),
    ("it", "12 mesi en italiano"),
    ("pt", "12 meses en portugués"),
])
def test_web_months_12_entradas(lang, _desc):
    months = _load(lang)["web"]["months"]
    assert len(months) == 12
    assert all(isinstance(m, str) and len(m) >= 2 for m in months)
