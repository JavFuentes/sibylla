"""Tests para el sidecar de traduccion del APOD de hoy (sibylla/apod.py).

Cubre fetch_apod (requests mockeado, sin red real) y build_apod_i18n (con
translate_cards monkeypatcheado, sin llamar al LLM real). Sigue el mismo molde
que test_translate.py y test_stellar.py: logica pura, cero red.
"""
from __future__ import annotations

import requests

from sibylla.apod import build_apod_i18n, fetch_apod

_APOD = {
    "date": "2026-06-30",
    "title": "A Galaxy Far Far Away",
    "explanation": "Long English explanation of the picture.",
    "url": "https://apod.nasa.gov/apod/image/2606/galaxy.jpg",
    "media_type": "image",
}


# ---------------------------------------------------------------------------
# fetch_apod
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def test_fetch_apod_feliz(monkeypatch):
    """Respuesta valida de NASA se devuelve tal cual."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_APOD))
    assert fetch_apod("DEMO_KEY") == _APOD


def test_fetch_apod_http_error_devuelve_none(monkeypatch):
    """Un error HTTP (p.ej. rate limit) no rompe: devuelve None."""
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({}, status=429))
    assert fetch_apod("DEMO_KEY") is None


def test_fetch_apod_excepcion_red_devuelve_none(monkeypatch):
    """Un fallo de transporte (timeout, DNS...) tampoco rompe: devuelve None."""
    def _raise(*a, **k):
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(requests, "get", _raise)
    assert fetch_apod("DEMO_KEY") is None


def test_fetch_apod_respuesta_incompleta_devuelve_none(monkeypatch):
    """Falta 'explanation' -> se descarta en vez de propagar un payload cojo."""
    incompleta = {"date": "2026-06-30", "title": "X"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(incompleta))
    assert fetch_apod("DEMO_KEY") is None


# ---------------------------------------------------------------------------
# build_apod_i18n
# ---------------------------------------------------------------------------
def test_build_apod_i18n_sin_traduccion_solo_ingles():
    """translate=False -> el payload solo trae 'en', sin tocar el cache/LLM."""
    payload = build_apod_i18n(_APOD, translate=False)
    assert payload["date"] == "2026-06-30"
    assert payload["title"] == {"en": "A Galaxy Far Far Away"}
    assert payload["explanation"] == {"en": "Long English explanation of the picture."}
    assert payload["schema"] == "cl.sibylla.apod_i18n.v1"


def test_build_apod_i18n_con_traduccion_agrega_es_it(monkeypatch):
    """Con traduccion exitosa, 'es' e 'it' se agregan a title y explanation."""
    def _fake_translate_cards(cards, lang, cache, tracker=None):
        card = cards[0]
        return {card["id"]: {"title": f"[{lang}] {card['title']}",
                             "snippet": f"[{lang}] {card['snippet']}"}}
    monkeypatch.setattr("sibylla.apod.translate_cards", _fake_translate_cards)
    monkeypatch.setattr("sibylla.apod.load_cache", lambda: {})
    monkeypatch.setattr("sibylla.apod.save_cache", lambda cache: None)

    payload = build_apod_i18n(_APOD, translate=True)

    assert payload["title"]["es"] == "[es] A Galaxy Far Far Away"
    assert payload["title"]["it"] == "[it] A Galaxy Far Far Away"
    assert payload["explanation"]["es"] == "[es] Long English explanation of the picture."
    assert payload["explanation"]["it"] == "[it] Long English explanation of the picture."
    assert payload["title"]["en"] == "A Galaxy Far Far Away"  # el original no se pierde


def test_build_apod_i18n_traduccion_fallida_cae_a_ingles(monkeypatch):
    """Si translate_cards no devuelve nada (LLM caido), el idioma simplemente no se agrega."""
    monkeypatch.setattr("sibylla.apod.translate_cards", lambda cards, lang, cache, tracker=None: {})
    monkeypatch.setattr("sibylla.apod.load_cache", lambda: {})
    monkeypatch.setattr("sibylla.apod.save_cache", lambda cache: None)

    payload = build_apod_i18n(_APOD, translate=True)

    assert payload["title"] == {"en": "A Galaxy Far Far Away"}
    assert payload["explanation"] == {"en": "Long English explanation of the picture."}


def test_build_apod_i18n_usa_id_namespaced_por_fecha(monkeypatch):
    """La card enviada a translate_cards usa el id 'apod:{date}' (evita colisionar
    con dedup_keys de noticias en el mismo cache compartido)."""
    seen_ids = []

    def _fake_translate_cards(cards, lang, cache, tracker=None):
        seen_ids.append(cards[0]["id"])
        return {}
    monkeypatch.setattr("sibylla.apod.translate_cards", _fake_translate_cards)
    monkeypatch.setattr("sibylla.apod.load_cache", lambda: {})
    monkeypatch.setattr("sibylla.apod.save_cache", lambda cache: None)

    build_apod_i18n(_APOD, translate=True)

    assert seen_ids == ["apod:2026-06-30", "apod:2026-06-30"]  # una llamada por idioma (es, it)
