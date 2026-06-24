"""Tests para la traducción de tarjetas (lógica pura, sin red).

Cubre el cache (split por aciertos, invalidación por src_title, round-trip
de disco), el parseo robusto de la respuesta del LLM (tolerante a ``` y prosa,
filtra ids desconocidos) y el comportamiento sin LLM de translate_cards.
La parte de red (la llamada al proveedor) se deja fuera, como el resto de
fetchers/LLM del proyecto.
"""

import json
from sibylla.models import LLMResponse

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


# ---------------------------------------------------------------------------
# translate_cards — reintento de ids faltantes (proveedor falso, sin red)
# ---------------------------------------------------------------------------
class _FakeProvider:
    """Proveedor de prueba: devuelve respuestas predefinidas, una por llamada."""
    name = "fake"
    model = "fake-1"

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def complete(self, system, user, **kwargs):
        resp = self._responses[self.calls]
        self.calls += 1
        return LLMResponse(text=resp)


def test_translate_cards_reintenta_los_faltantes(monkeypatch):
    """Si el 1er lote omite un id, se reintenta solo ese y se completa."""
    cards = [{"id": "u:1", "title": "A", "snippet": "a"},
             {"id": "u:2", "title": "B", "snippet": "b"}]
    resp1 = json.dumps([{"id": "u:1", "title": "TA", "snippet": "sa"}])  # omite u:2
    resp2 = json.dumps([{"id": "u:2", "title": "TB", "snippet": "sb"}])  # lo devuelve
    fake = _FakeProvider([resp1, resp2])
    monkeypatch.setattr("sibylla.translate.get_provider", lambda: fake)
    out = translate_cards(cards, "pt", {})
    assert out == {"u:1": {"title": "TA", "snippet": "sa"},
                   "u:2": {"title": "TB", "snippet": "sb"}}
    assert fake.calls == 2  # hubo exactamente un reintento


def test_translate_cards_reintento_acotado_a_uno(monkeypatch):
    """Si tras el reintento sigue faltando un id, no se reintenta más; cae al original."""
    cards = [{"id": "u:1", "title": "A", "snippet": "a"},
             {"id": "u:2", "title": "B", "snippet": "b"}]
    resp = json.dumps([{"id": "u:1", "title": "TA", "snippet": "sa"}])  # siempre omite u:2
    fake = _FakeProvider([resp, resp])
    monkeypatch.setattr("sibylla.translate.get_provider", lambda: fake)
    out = translate_cards(cards, "pt", {})
    assert out == {"u:1": {"title": "TA", "snippet": "sa"}}  # u:2 no está -> original
    assert fake.calls == 2  # 1 lote + 1 reintento, y se detiene


# ---------------------------------------------------------------------------
# translate_cards — troceado en lotes y truncamiento (proveedor falso, sin red)
# ---------------------------------------------------------------------------
class _TruncFakeProvider:
    """Proveedor que simula truncamiento: output pega max_tokens y el JSON queda cortado."""
    name = "fake"
    model = "fake-1"

    def __init__(self):
        self.calls = 0

    def complete(self, system, user, **kwargs):
        self.calls += 1
        mt = kwargs.get("max_tokens", 6000)
        # JSON a medias (sin cerrar) + output == tope -> senal de truncamiento.
        return LLMResponse(text='[{"id": "u:1", "title": "Cor',
                           usage={"input": 100, "output": mt})


def test_translate_cards_trocea_en_lotes(monkeypatch):
    """Muchas tarjetas se traducen en una llamada por chunk; todas quedan traducidas."""
    monkeypatch.setattr("sibylla.translate._CHUNK_SIZE", 2)
    cards = [{"id": f"u:{i}", "title": f"T{i}", "snippet": f"s{i}"} for i in range(1, 5)]
    resp1 = json.dumps([{"id": "u:1", "title": "X1", "snippet": "y1"},
                        {"id": "u:2", "title": "X2", "snippet": "y2"}])  # chunk 1
    resp2 = json.dumps([{"id": "u:3", "title": "X3", "snippet": "y3"},
                        {"id": "u:4", "title": "X4", "snippet": "y4"}])  # chunk 2
    fake = _FakeProvider([resp1, resp2])
    monkeypatch.setattr("sibylla.translate.get_provider", lambda: fake)
    out = translate_cards(cards, "pt", {})
    assert out == {"u:1": {"title": "X1", "snippet": "y1"},
                   "u:2": {"title": "X2", "snippet": "y2"},
                   "u:3": {"title": "X3", "snippet": "y3"},
                   "u:4": {"title": "X4", "snippet": "y4"}}
    assert fake.calls == 2  # 2 chunks -> 2 llamadas, sin reintentos


def test_translate_cards_truncamiento_no_reintenta(monkeypatch):
    """Si el output pega max_tokens (JSON cortado), no se reintenta el lote: 1 sola llamada."""
    cards = [{"id": "u:1", "title": "A", "snippet": "a"},
             {"id": "u:2", "title": "B", "snippet": "b"}]
    fake = _TruncFakeProvider()
    monkeypatch.setattr("sibylla.translate.get_provider", lambda: fake)
    out = translate_cards(cards, "pt", {}, max_tokens=6000)
    assert out == {} and fake.calls == 1  # truncó -> break, sin reintento
