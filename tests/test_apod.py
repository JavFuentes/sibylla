"""Tests para el sidecar de traduccion del APOD de hoy (sibylla/apod.py).

Cubre fetch_apod (requests mockeado, sin red real), build_apod_i18n (con
translate_cards monkeypatcheado, sin llamar al LLM real) y write_apod_sidecar
(sibylla/web.py: escritura de apod-i18n.json + su copia historica en
apod-i18n/<fecha>.json). Sigue el mismo molde que test_translate.py y
test_stellar.py: logica pura, cero red.
"""
from __future__ import annotations

import json

import requests

import sibylla.apod as apod_mod
import sibylla.web as web_mod
from sibylla.apod import build_apod_i18n, fetch_apod
from sibylla.web import write_apod_sidecar

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
            exc = requests.HTTPError(f"HTTP {self.status_code}")
            exc.response = self  # requests real adjunta la respuesta; fetch_apod lee .status_code de ahi
            raise exc

    def json(self):
        return self._payload


def test_fetch_apod_feliz(monkeypatch):
    """Respuesta valida de NASA se devuelve tal cual, sin reintentar."""
    calls = []
    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse(_APOD)
    monkeypatch.setattr(requests, "get", _get)
    assert fetch_apod("DEMO_KEY") == _APOD
    assert len(calls) == 1


def test_fetch_apod_503_persistente_agota_reintentos_y_devuelve_none(monkeypatch):
    """Un 503 persistente (fallo transitorio de NASA) se reintenta `attempts`
    veces y, si nunca se recupera, devuelve None sin lanzar."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    calls = []
    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse({}, status=503)
    monkeypatch.setattr(requests, "get", _get)
    assert fetch_apod("DEMO_KEY", attempts=3) is None
    assert len(calls) == 3


def test_fetch_apod_503_se_recupera_en_reintento(monkeypatch):
    """Si el primer intento falla con un codigo transitorio pero el segundo
    responde bien, el reintento recupera el APOD (no hace falta esperar al
    cron de respaldo)."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    respuestas = [_FakeResponse({}, status=503), _FakeResponse(_APOD)]
    monkeypatch.setattr(requests, "get", lambda *a, **k: respuestas.pop(0))
    assert fetch_apod("DEMO_KEY") == _APOD


def test_fetch_apod_error_permanente_no_reintenta(monkeypatch):
    """Un 401 (API key invalida) es un error permanente: reintentar no lo
    arregla, asi que se abandona en el primer intento."""
    calls = []
    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse({}, status=401)
    monkeypatch.setattr(requests, "get", _get)
    assert fetch_apod("DEMO_KEY") is None
    assert len(calls) == 1


def test_fetch_apod_excepcion_red_reintenta_y_devuelve_none(monkeypatch):
    """Un fallo de transporte (timeout, DNS...) es transitorio: se reintenta,
    y si persiste, no rompe (devuelve None)."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    calls = []
    def _raise(*a, **k):
        calls.append(1)
        raise requests.ConnectionError("boom")
    monkeypatch.setattr(requests, "get", _raise)
    assert fetch_apod("DEMO_KEY", attempts=3) is None
    assert len(calls) == 3


def test_fetch_apod_respuesta_incompleta_devuelve_none(monkeypatch):
    """Falta 'explanation' -> se descarta en vez de propagar un payload cojo."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
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


# ---------------------------------------------------------------------------
# write_apod_sidecar (sibylla/web.py): apod-i18n.json + copia historica
# ---------------------------------------------------------------------------
def _patch_sidecar_deps(monkeypatch, tmp_path, apod=_APOD):
    """Aisla write_apod_sidecar de red/env/LLM: SITE_DIR a tmp_path, fetch_apod
    fijo, sin .env real y sin traduccion (translate=False en los tests que no
    la necesiten evita tocar el cache/LLM)."""
    monkeypatch.setattr(web_mod, "SITE_DIR", tmp_path)
    monkeypatch.setattr(web_mod, "load_env", lambda: None)
    monkeypatch.setattr(web_mod, "get_nasa_api_key", lambda: "DEMO_KEY")
    monkeypatch.setattr(web_mod, "fetch_apod", lambda api_key: apod)


def test_write_apod_sidecar_escribe_archivo_de_hoy_y_copia_historica(monkeypatch, tmp_path):
    """Cada corrida deja DOS archivos: el sidecar de hoy (se pisa siempre) y
    una copia en apod-i18n/<fecha>.json (el archivo historico)."""
    _patch_sidecar_deps(monkeypatch, tmp_path)

    path = write_apod_sidecar(translate=False)

    assert path == tmp_path / "apod-i18n.json"
    assert path.exists()
    archivo = tmp_path / "apod-i18n" / "2026-06-30.json"
    assert archivo.exists()
    # Mismo contenido en ambos (misma corrida, mismo payload).
    assert json.loads(path.read_text(encoding="utf-8")) == json.loads(archivo.read_text(encoding="utf-8"))


def test_write_apod_sidecar_copia_historica_no_se_pisa_entre_fechas(monkeypatch, tmp_path):
    """Una corrida de un dia posterior no borra la copia historica del dia
    anterior: cada fecha vive en su propio archivo dentro de apod-i18n/."""
    _patch_sidecar_deps(monkeypatch, tmp_path, apod=_APOD)
    write_apod_sidecar(translate=False)

    apod_manana = {**_APOD, "date": "2026-07-01", "title": "Another Nebula"}
    _patch_sidecar_deps(monkeypatch, tmp_path, apod=apod_manana)
    write_apod_sidecar(translate=False)

    archivo_dir = tmp_path / "apod-i18n"
    assert (archivo_dir / "2026-06-30.json").exists()  # no se borro
    assert (archivo_dir / "2026-07-01.json").exists()
    hoy = json.loads((tmp_path / "apod-i18n.json").read_text(encoding="utf-8"))
    assert hoy["date"] == "2026-07-01"  # el sidecar de "hoy" si se piso


def test_write_apod_sidecar_sin_apod_no_escribe_nada(monkeypatch, tmp_path):
    """Si NASA no responde (fetch_apod -> None), no se escribe ni el sidecar
    ni la copia historica; write_apod_sidecar devuelve None."""
    _patch_sidecar_deps(monkeypatch, tmp_path, apod=None)

    assert write_apod_sidecar(translate=False) is None
    assert not (tmp_path / "apod-i18n.json").exists()
    assert not (tmp_path / "apod-i18n").exists()
