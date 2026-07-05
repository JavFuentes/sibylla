"""Tests de la sincronización build-time de conteos sociales."""

import requests

from sibylla import social_sync


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def test_fetch_conteos_parsea_rest_firestore(monkeypatch):
    payload = {"fields": {"n-abc": {"mapValue": {"fields": {
        "l": {"integerValue": "3"}, "d": {"integerValue": "1"}, "c": {"integerValue": "2"},
    }}}}}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert social_sync.fetch_conteos("KEY", "proj") == {"n-abc": {"l": 3, "d": 1, "c": 2}}


def test_fetch_conteos_timeout_devuelve_vacio(monkeypatch):
    def _boom(*_a, **_k):
        raise requests.Timeout("timeout")
    monkeypatch.setattr(requests, "get", _boom)
    assert social_sync.fetch_conteos("KEY", "proj") == {}


def test_fetch_conteos_respuesta_malformada_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"fields": []}))
    assert social_sync.fetch_conteos("KEY", "proj") == {}
