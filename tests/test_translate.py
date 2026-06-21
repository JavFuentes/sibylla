"""Tests para la traducción de tarjetas (lógica pura, sin red).

Cubre el cache (split por aciertos, invalidación por src_title, round-trip
de disco), el parseo robusto de la respuesta del LLM (tolerante a ``` y prosa,
filtra ids desconocidos) y el comportamiento sin LLM de translate_cards.
La parte de red (la llamada al proveedor) se deja fuera, como el resto de
fetchers/LLM del proyecto.
"""

import json

import pytest

from sibylla.translate import (
    _extract_json_array,
    _parse_response,
    _split_by_cache,
    load_cache,
    save_cache,
    translate_cards,
)


def _card(id="u:https://x.com/a", title="Quantum chip breakthrough", snippet="A new chip…"):
    return {"id": id, "title": title, "snippet": snippet}


# ---------------------------------------------------------------------------
# _split_by_cache
# ---------------------------------------------------------------------------
def test_split_cache_acierto_vigente():
    """Una entrada cacheada con el mismo src_title cuenta como hit."""
    cache = {"pt": {"u:1": {"title": "Trad", "snippet": "Frag", "src_title": "Orig"}}}
    cards = [{"id": "u:1", "title": "Orig", "snippet": "x"}]
    hits, misses = _split_by_cache(cards, "pt", cache)
    assert hits == {"u:1": {"title": "Trad", "snippet": "Frag"}} and misses == []


def test_split_cache_miss_si_no_existe():
    """Un id ausente del cache es un miss."""
    cards = [{"id": "u:1", "title": "Orig", "snippet": "x"}]
    hits, misses = _split_by_cache(cards, "pt", {})
    assert hits == {} and [c["id"] for c in misses] == ["u:1"]


def test_split_cache_invalida_si_cambia_src_title():
    """Si el título de la fuente cambió, la traducción cacheada se invalida (miss)."""
    cache = {"pt": {"u:1": {"title": "Trad vieja", "snippet": "v", "src_title": "Título viejo"}}}
    cards = [{"id": "u:1", "title": "Título NUEVO", "snippet": "x"}]
    hits, misses = _split_by_cache(cards, "pt", cache)
    assert hits == {} and [c["id"] for c in misses] == ["u:1"]


def test_split_cache_idioma_aislado():
    """El cache de un idioma no sirve traducciones para otro."""
    cache = {"en": {"u:1": {"title": "T", "snippet": "S", "src_title": "Orig"}}}
    cards = [{"id": "u:1", "title": "Orig", "snippet": "x"}]
    hits, misses = _split_by_cache(cards, "pt", cache)
    assert hits == {} and len(misses) == 1


# ---------------------------------------------------------------------------
# _extract_json_array
# ---------------------------------------------------------------------------
EXTRACT_CASES = [
    ('[{"id": "a"}]', [{"id": "a"}], "array JSON pelado"),
    ('```json\n[{"id": "a"}]\n```', [{"id": "a"}], "array dentro de fences markdown"),
    ('Claro, aquí tienes:\n[{"id": "a"}]\nEspero ayude.', [{"id": "a"}], "array rodeado de prosa"),
    ("no hay json aquí", None, "sin array → None"),
    ("[roto", None, "array malformado → None"),
]


@pytest.mark.parametrize("raw,esperado,_desc", EXTRACT_CASES)
def test_extract_json_array(raw, esperado, _desc):
    assert _extract_json_array(raw) == esperado


# ---------------------------------------------------------------------------
# _parse_response
# ---------------------------------------------------------------------------
def test_parse_response_feliz():
    """Filas válidas con id conocido se convierten a {id: {title, snippet}}."""
    raw = '[{"id": "u:1", "title": "Título", "snippet": "Frag"}]'
    out = _parse_response(raw, valid_ids={"u:1"})
    assert out == {"u:1": {"title": "Título", "snippet": "Frag"}}


def test_parse_response_filtra_id_desconocido():
    """Un id que no estaba en la petición se descarta."""
    raw = '[{"id": "u:OTRO", "title": "X", "snippet": "Y"}]'
    assert _parse_response(raw, valid_ids={"u:1"}) == {}


def test_parse_response_snippet_ausente_queda_vacio():
    """Si falta snippet, queda como cadena vacía (no rompe)."""
    raw = '[{"id": "u:1", "title": "Solo título"}]'
    assert _parse_response(raw, valid_ids={"u:1"}) == {"u:1": {"title": "Solo título", "snippet": ""}}


def test_parse_response_descarta_titulo_vacio():
    """Una fila con título vacío se ignora (no aporta traducción)."""
    raw = '[{"id": "u:1", "title": "   ", "snippet": "Y"}]'
    assert _parse_response(raw, valid_ids={"u:1"}) == {}


def test_parse_response_no_parseable_devuelve_vacio():
    """Respuesta sin JSON → {} (las tarjetas caerán al original)."""
    assert _parse_response("lo siento, no puedo", valid_ids={"u:1"}) == {}


# ---------------------------------------------------------------------------
# load_cache / save_cache (round-trip en disco)
# ---------------------------------------------------------------------------
def test_cache_round_trip(tmp_path):
    """Lo guardado se recupera idéntico."""
    path = tmp_path / "translations.json"
    data = {"pt": {"u:1": {"title": "T", "snippet": "S", "src_title": "O"}}}
    save_cache(data, path)
    assert load_cache(path) == data


def test_load_cache_inexistente_es_vacio(tmp_path):
    """Sin archivo → {} (primera corrida)."""
    assert load_cache(tmp_path / "no-existe.json") == {}


def test_load_cache_corrupto_es_vacio(tmp_path):
    """JSON inválido → {} (degradación elegante, no crash)."""
    path = tmp_path / "translations.json"
    path.write_text("{esto no es json", encoding="utf-8")
    assert load_cache(path) == {}


def test_save_cache_crea_directorio(tmp_path):
    """save_cache crea el directorio padre si no existe."""
    path = tmp_path / "data" / "translations.json"
    save_cache({"es": {}}, path)
    assert path.exists()


# ---------------------------------------------------------------------------
# translate_cards — comportamiento sin LLM (no toca la red)
# ---------------------------------------------------------------------------
def test_translate_cards_sin_llm_devuelve_solo_cache(monkeypatch):
    """Sin LLM configurado, solo se devuelven los aciertos del cache; nada de red."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    cache = {"pt": {"u:1": {"title": "Trad", "snippet": "Frag", "src_title": "Orig"}}}
    cards = [
        {"id": "u:1", "title": "Orig", "snippet": "x"},   # en cache
        {"id": "u:2", "title": "Otro", "snippet": "y"},   # no traducible sin LLM
    ]
    out = translate_cards(cards, "pt", cache)
    assert out == {"u:1": {"title": "Trad", "snippet": "Frag"}}


def test_translate_cards_vacio(monkeypatch):
    """Sin tarjetas, no hay nada que traducir."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert translate_cards([], "pt", {}) == {}


def test_translate_cards_todo_en_cache_no_necesita_llm(monkeypatch):
    """Si todo está cacheado, devuelve los hits sin intentar proveedor."""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")  # aunque haya provider, no debe llamarse
    cache = {"en": {"u:1": {"title": "T", "snippet": "S", "src_title": "Orig"}}}
    cards = [{"id": "u:1", "title": "Orig", "snippet": "x"}]
    out = translate_cards(cards, "en", cache)
    assert out == {"u:1": {"title": "T", "snippet": "S"}}
