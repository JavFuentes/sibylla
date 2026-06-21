"""Tests para internacionalización: resolve_lang, t(), load_translations.

Cubre la prioridad de idioma (flag > env > config > 'es'), el acceso con
notación de puntos, el fallback ??clave??, y la carga de archivos JSON.
"""

import pytest

from sibylla.i18n import LANG_NAMES, load_translations, resolve_lang, t


# ---------------------------------------------------------------------------
# resolve_lang — prioridades
# ---------------------------------------------------------------------------
def test_resolve_lang_flag_tiene_prioridad():
    """El flag --lang del CLI gana sobre todo lo demás."""
    assert resolve_lang(cli_lang="en", config_meta={"default_user_language": "pt"}) == "en"


def test_resolve_lang_env_var(monkeypatch):
    """La variable de entorno SIBYLLA_LANG se usa cuando no hay flag."""
    monkeypatch.setenv("SIBYLLA_LANG", "it")
    assert resolve_lang() == "it"


def test_resolve_lang_config_sin_flag_ni_env(monkeypatch):
    """Config default_user_language aplica si no hay flag ni env."""
    monkeypatch.delenv("SIBYLLA_LANG", raising=False)
    assert resolve_lang(config_meta={"default_user_language": "pt"}) == "pt"


def test_resolve_lang_fallback_es_cuando_nada():
    """Sin flag, env ni config -> 'es'."""
    assert resolve_lang() == "es"


def test_resolve_lang_idioma_inexistente_cae_a_es():
    """Si el archivo del idioma no existe (fr.json), cae a 'es'."""
    assert resolve_lang(cli_lang="fr") == "es"


def test_resolve_lang_whitespace_strip():
    """Flag con espacios se limpia."""
    assert resolve_lang(cli_lang=" en ") == "en"


def test_resolve_lang_mayusculas_se_normaliza():
    """Flag en mayúsculas se pasa a minúsculas."""
    assert resolve_lang(cli_lang="EN") == "en"


def test_resolve_lang_flag_vacio_cae_a_config():
    """Flag cadena vacía o solo espacios → siguiente prioridad."""
    assert resolve_lang(cli_lang="", config_meta={"default_user_language": "it"}) == "it"


def test_resolve_lang_config_sin_clave_cae_a_es(monkeypatch):
    """Config sin la clave default_user_language → fallback 'es'."""
    monkeypatch.delenv("SIBYLLA_LANG", raising=False)
    assert resolve_lang(config_meta={"otra": "cosa"}) == "es"


# ---------------------------------------------------------------------------
# t() — acceso con notación de puntos y fallback
# ---------------------------------------------------------------------------

def test_t_acceso_punto_simple():
    """Acceso a una clave hoja con notación de puntos."""
    tr = load_translations("es")
    assert t(tr, "digest.no_date") == "s/f"


def test_t_nodo_intermedio_devuelve_string():
    """Si la ruta apunta a un dict, se convierte a string."""
    tr = load_translations("es")
    result = t(tr, "digest")
    assert isinstance(result, str)
    assert len(result) > 0


def test_t_clave_inexistente_fallback():
    """Clave inexistente → ??clave??."""
    tr = load_translations("es")
    assert t(tr, "inexistente.clave") == "??inexistente.clave??"


def test_t_clave_anidada_inexistente_fallback():
    """Rama existe pero la hoja no → ??clave??."""
    tr = load_translations("es")
    assert t(tr, "digest.key_que_no_existe") == "??digest.key_que_no_existe??"


def test_t_interpolacion_kwargs():
    """Los kwargs se interpolan con str.format()."""
    tr = load_translations("es")
    result = t(tr, "cli.result_line", count=5, mode="IA", path="out.md")
    assert "5" in result
    assert "IA" in result
    assert "out.md" in result


def test_t_falta_kwarg_no_crashea():
    """Si falta un kwarg, no se lanza excepción (el placeholder queda)."""
    tr = load_translations("es")
    result = t(tr, "cli.result_line", count=7)
    # No crashea; puede contener o no el placeholder sin reemplazar.
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# load_translations
# ---------------------------------------------------------------------------
def test_load_translations_exitoso():
    data = load_translations("es")
    assert isinstance(data, dict)
    assert "cli" in data
    assert "digest" in data
    assert "web" in data
    assert data["_meta"]["code"] == "es"


def test_load_translations_idioma_inexistente():
    with pytest.raises(FileNotFoundError):
        load_translations("zz")


# ---------------------------------------------------------------------------
# LANG_NAMES — integridad
# ---------------------------------------------------------------------------
def test_lang_names_tiene_los_cuatro_idiomas():
    assert set(LANG_NAMES.keys()) == {"es", "en", "it", "pt"}
    assert LANG_NAMES["es"] == "español"
    assert LANG_NAMES["en"] == "English"
    assert LANG_NAMES["it"] == "italiano"
    assert LANG_NAMES["pt"] == "português"
