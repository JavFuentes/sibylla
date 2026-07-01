# -*- coding: utf-8 -*-
"""Tests del fetcher de YouTube vía Data API v3 (sección Divulgación).

Cubre _yt_channel_id, _youtube_api_fetch (requests mockeado, sin red) y la
preferencia de fetch_youtube por la API cuando hay clave, con fallback al RSS.
Sigue el molde de test_apod.py: lógica pura, cero red, cero disco.
"""
from __future__ import annotations

import requests

from sibylla import fetchers
from sibylla.config import Source
from sibylla.fetchers import _youtube_api_fetch, _yt_channel_id, fetch_youtube

_CID = "UCbdSYaPD-lr1kW27UJuk8Pw"
_FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={_CID}"


def _source(**over) -> Source:
    base = dict(id="yt_test", name="Canal Test", tier=3, type="rss",
                topics=["divulgacion"], url=_FEED_URL, raw={"category": "youtube"})
    base.update(over)
    return Source(**base)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


_API_PAYLOAD = {
    "items": [
        {"snippet": {"resourceId": {"videoId": "vid1"}, "title": "Primer video",
                     "publishedAt": "2026-06-30T10:00:00Z", "description": "desc 1"}},
        {"snippet": {"resourceId": {"videoId": "vidX"}, "title": "Private video",
                     "publishedAt": "2026-06-29T10:00:00Z"}},          # hueco privado -> se salta
        {"snippet": {"resourceId": {"videoId": ""}, "title": "sin id"}},  # sin videoId -> se salta
        {"snippet": {"resourceId": {"videoId": "vid3"}, "title": "Tercer video",
                     "publishedAt": "2026-06-28T10:00:00Z"}},
    ]
}


# ---------------------------------------------------------------------------
# _yt_channel_id
# ---------------------------------------------------------------------------
def test_channel_id_desde_url():
    assert _yt_channel_id(_source()) == _CID


def test_channel_id_prefiere_campo_raw():
    src = _source(raw={"category": "youtube", "channel_id": "UCotroIDdelYAML000000x"})
    assert _yt_channel_id(src) == "UCotroIDdelYAML000000x"


def test_channel_id_url_sin_parametro_devuelve_vacio():
    assert _yt_channel_id(_source(url="https://www.youtube.com/@handle", raw={})) == ""


# ---------------------------------------------------------------------------
# _youtube_api_fetch
# ---------------------------------------------------------------------------
def test_api_feliz_mapea_y_filtra(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_API_PAYLOAD))
    items = _youtube_api_fetch(_source(), limit=6, api_key="KEY")
    assert [it.extra["video_id"] for it in items] == ["vid1", "vid3"]  # privado y sin-id fuera
    it = items[0]
    assert it.title == "Primer video"
    assert it.url == "https://www.youtube.com/watch?v=vid1"
    assert it.image == "https://i.ytimg.com/vi/vid1/hqdefault.jpg"
    assert it.extra["kind"] == "video"
    assert it.published is not None and it.published.year == 2026


def test_api_respeta_el_limite(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_API_PAYLOAD))
    assert len(_youtube_api_fetch(_source(), limit=1, api_key="KEY")) == 1


def test_api_403_cuota_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({}, status=403))
    assert _youtube_api_fetch(_source(), limit=6, api_key="KEY") == []


def test_api_error_red_devuelve_vacio(monkeypatch):
    def _boom(*a, **k):
        raise requests.ConnectionError("timeout")
    monkeypatch.setattr(requests, "get", _boom)
    assert _youtube_api_fetch(_source(), limit=6, api_key="KEY") == []


def test_api_channel_id_no_UC_devuelve_vacio(monkeypatch):
    # Si no hay channel_id UC..., ni siquiera se llama a la red.
    def _fail(*a, **k):
        raise AssertionError("no debería llamarse a requests.get")
    monkeypatch.setattr(requests, "get", _fail)
    assert _youtube_api_fetch(_source(url="https://x/@h", raw={}), limit=6, api_key="KEY") == []


# ---------------------------------------------------------------------------
# fetch_youtube: preferencia API + fallback
# ---------------------------------------------------------------------------
def test_fetch_youtube_prefiere_api_y_siembra_cache(monkeypatch):
    monkeypatch.setattr(fetchers, "get_youtube_api_key", lambda: "KEY")
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_API_PAYLOAD))
    sembrado = {}
    monkeypatch.setattr(fetchers, "_yt_cache_put",
                        lambda sid, items: sembrado.update({sid: len(items)}))
    # Si tocara el RSS, esto lo delataría (no debería dormir ni parsear feed):
    monkeypatch.setattr(fetchers.time, "sleep",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("no debe caer al RSS")))

    items = fetch_youtube(_source(), limit=6)
    assert [it.extra["video_id"] for it in items] == ["vid1", "vid3"]
    assert sembrado == {"yt_test": 2}


def test_fetch_youtube_sin_clave_no_llama_api(monkeypatch):
    monkeypatch.setattr(fetchers, "get_youtube_api_key", lambda: "")
    monkeypatch.setattr(fetchers, "_youtube_api_fetch",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("sin clave no se usa la API")))
    monkeypatch.setattr(fetchers.time, "sleep", lambda *_a, **_k: None)
    # El feed RSS devuelve un código de retry -> sin entradas; caché vacío -> [].
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(b"", status=503))
    monkeypatch.setattr(fetchers, "_yt_cache_get", lambda *a, **k: [])
    assert fetch_youtube(_source(), limit=6, attempts=1) == []


def test_fetch_youtube_api_vacia_cae_al_rss(monkeypatch):
    monkeypatch.setattr(fetchers, "get_youtube_api_key", lambda: "KEY")
    monkeypatch.setattr(fetchers, "_youtube_api_fetch", lambda *a, **k: [])  # cuota/quota fallo
    monkeypatch.setattr(fetchers.time, "sleep", lambda *_a, **_k: None)
    llamado = {"rss": False}

    def _fake_get(*a, **k):
        llamado["rss"] = True
        return _FakeResponse(b"", status=503)
    monkeypatch.setattr(requests, "get", _fake_get)
    monkeypatch.setattr(fetchers, "_yt_cache_get", lambda *a, **k: [])
    assert fetch_youtube(_source(), limit=6, attempts=1) == []
    assert llamado["rss"] is True  # sí intentó el fallback RSS
