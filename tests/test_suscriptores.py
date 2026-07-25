from sibylla import suscriptores as su


def _doc(uid="u1", email="a@example.com", activa=True, temas=("ai",), v=1):
    return {"fields": {
        "uid": {"stringValue": uid}, "email": {"stringValue": email},
        "activa": {"booleanValue": activa}, "v": {"integerValue": str(v)},
        "temas": {"arrayValue": {"values": [{"stringValue": t} for t in temas]}},
    }}


def test_fetch_pagina_y_descarta_invalidos(monkeypatch):
    import sys
    import types
    class Creds:
        token = "tok"
        def refresh(self, _request): pass
    class Resp:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload
    payloads = iter([
        {"documents": [_doc(), _doc("u2", "mal", temas=("ai",))], "nextPageToken": "p2"},
        {"documents": [_doc("u3", "c@example.com", temas=("desconocido", "medicine"))]},
    ])
    monkeypatch.setattr(su, "load_sa_credentials", lambda: Creds())
    monkeypatch.setattr(su.requests, "get", lambda *a, **k: Resp(next(payloads)))
    transport = types.ModuleType("google.auth.transport.requests")
    transport.Request = lambda: object()
    monkeypatch.setitem(sys.modules, "google", types.ModuleType("google"))
    monkeypatch.setitem(sys.modules, "google.auth", types.ModuleType("google.auth"))
    monkeypatch.setitem(sys.modules, "google.auth.transport", types.ModuleType("google.auth.transport"))
    monkeypatch.setitem(sys.modules, "google.auth.transport.requests", transport)
    got = su.fetch_suscriptores()
    assert [(s.uid, s.temas) for s in got] == [("u1", ("ai",)), ("u3", ("medicine",))]


def test_fetch_fallo_devuelve_vacio(monkeypatch):
    monkeypatch.setattr(su, "load_sa_credentials", lambda: None)
    assert su.fetch_suscriptores() == []
