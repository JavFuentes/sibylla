"""Tests para el sidecar de traduccion del APOD de hoy (sibylla/apod.py).

Cubre fetch_apod (requests mockeado, sin red real), build_apod_i18n (con
translate_cards monkeypatcheado, sin llamar al LLM real) y write_apod_sidecar
(sibylla/web.py: escritura de apod-i18n.json + su copia historica en
apod-i18n/<fecha>.json). Sigue el mismo molde que test_translate.py y
test_stellar.py: logica pura, cero red.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import requests

import sibylla.apod as apod_mod
import sibylla.web as web_mod
from sibylla.apod import APOD_SOURCE_ID, build_apod_card, build_apod_i18n, fetch_apod
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
    assert fetch_apod("DEMO_KEY", date=_APOD["date"]) == _APOD
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
    assert fetch_apod("DEMO_KEY", date=_APOD["date"], attempts=3) is None
    assert len(calls) == 3


def test_fetch_apod_503_se_recupera_en_reintento(monkeypatch):
    """Si el primer intento falla con un codigo transitorio pero el segundo
    responde bien, el reintento recupera el APOD (no hace falta esperar al
    cron de respaldo)."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    respuestas = [_FakeResponse({}, status=503), _FakeResponse(_APOD)]
    monkeypatch.setattr(requests, "get", lambda *a, **k: respuestas.pop(0))
    assert fetch_apod("DEMO_KEY", date=_APOD["date"]) == _APOD


def test_fetch_apod_error_permanente_no_reintenta(monkeypatch):
    """Un 401 (API key invalida) es un error permanente: reintentar no lo
    arregla, asi que se abandona en el primer intento."""
    calls = []
    def _get(*a, **k):
        calls.append(1)
        return _FakeResponse({}, status=401)
    monkeypatch.setattr(requests, "get", _get)
    assert fetch_apod("DEMO_KEY", date=_APOD["date"]) is None
    assert len(calls) == 1


@pytest.mark.parametrize("error", [requests.ConnectionError("boom"), requests.Timeout("timeout")])
def test_fetch_apod_excepcion_red_reintenta_y_devuelve_none(monkeypatch, error):
    """Un fallo de transporte (timeout, DNS...) es transitorio: se reintenta,
    y si persiste, no rompe (devuelve None)."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    calls = []
    def _raise(*a, **k):
        calls.append(1)
        raise error
    monkeypatch.setattr(requests, "get", _raise)
    assert fetch_apod("DEMO_KEY", date=_APOD["date"], attempts=3) is None
    assert len(calls) == 3


def test_fetch_apod_respuesta_incompleta_devuelve_none(monkeypatch):
    """Falta 'explanation' -> se descarta en vez de propagar un payload cojo."""
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *_a, **_k: None)
    incompleta = {"date": "2026-06-30", "title": "X"}
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(incompleta))
    assert fetch_apod("DEMO_KEY", date=_APOD["date"]) is None


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


# ---------------------------------------------------------------------------
# build_apod_card
# ---------------------------------------------------------------------------
_PAYLOAD_EN = {
    "schema": "cl.sibylla.apod_i18n.v1",
    "generated_at": "2026-06-30T07:17:00Z",
    "date": "2026-06-30",
    "title": {"en": "A Galaxy Far Far Away"},
    "explanation": {"en": "Long English explanation of the picture."},
}


def test_build_apod_card_imagen_basico():
    """Dia de imagen: source_id correcto, url en formato apYYMMDD.html, imagen del APOD."""
    from datetime import datetime, timezone
    card = build_apod_card(_APOD, _PAYLOAD_EN)
    assert card is not None
    assert card.source_id == APOD_SOURCE_ID
    assert card.source_name == "NASA APOD"
    assert card.tier == 1
    assert card.url == "https://apod.nasa.gov/apod/ap260630.html"
    assert card.image == _APOD["url"]
    assert card.title == "A Galaxy Far Far Away"
    assert "astronomia" in card.topics
    assert card.published == datetime(2026, 6, 30, tzinfo=timezone.utc)


def test_build_apod_card_video_con_thumbnail():
    """Dia de video con thumbnail_url: image usa el thumbnail."""
    apod_video = {**_APOD, "media_type": "video",
                  "thumbnail_url": "https://i.ytimg.com/vi/abc/hq.jpg"}
    card = build_apod_card(apod_video, _PAYLOAD_EN)
    assert card is not None
    assert card.image == "https://i.ytimg.com/vi/abc/hq.jpg"


def test_build_apod_card_video_sin_thumbnail():
    """Dia de video sin thumbnail: image es None (degrada al placeholder CSS)."""
    apod_video = {**_APOD, "media_type": "video"}
    card = build_apod_card(apod_video, _PAYLOAD_EN)
    assert card is not None
    assert card.image is None


def test_build_apod_card_dedup_key_estable():
    """dedup_key se basa en la URL (canonical): apod:date no colisiona con noticias."""
    card = build_apod_card(_APOD, _PAYLOAD_EN)
    assert card is not None
    # La URL canonicalizada determina el dedup_key; el mismo APOD siempre da el mismo key.
    assert card.dedup_key == build_apod_card(_APOD, _PAYLOAD_EN).dedup_key


def test_build_apod_card_datos_malformados_devuelve_none():
    """Si el payload no tiene 'en' en title, devuelve None sin lanzar."""
    malo = {"title": {}, "explanation": {"en": "x"}}
    result = build_apod_card(_APOD, malo)
    assert result is None


# Respuesta representativa de NASA Science: `url` es una página, no la imagen.
_WP_APOD = {
    "date": "2026-10-18",
    "title": "Sonified: Eagle Nebula Pillars",
    "explanation": "<strong>Explanation:</strong> Stars &amp; galaxies.<br>Tomorrow.",
    "media_type": "video",
    "url": "https://science.nasa.gov/image-article/apod-2026-october-18-sonified-eagle-nebula-pillars/",
    "permalink": "https://science.nasa.gov/image-article/apod-2026-october-18-sonified-eagle-nebula-pillars/",
    "hdurl": "https://assets.science.nasa.gov/dynamicimage/SonifiedEagleNebulaPillars.jpg?w=3048&h=1708",
    "basic_html": '<iframe src="//www.youtube.com/embed/DzrCEm1ZBRY?rel=0"></iframe>',
}


def test_fetch_apod_usa_ruta_por_fecha_y_solo_parametro_de_credencial(monkeypatch):
    seen = []

    def _get(url, **kwargs):
        seen.append((url, kwargs))
        return _FakeResponse(_WP_APOD)

    monkeypatch.setattr(requests, "get", _get)
    result = fetch_apod("test-key", date="2026-10-18", timeout=7)
    assert seen == [("https://science.nasa.gov/wp-json/wp/v2/apod-basic/261018",
                     {"params": {"api_key": "test-key"}, "timeout": 7})]
    assert result["explanation"] == "Explanation: Stars & galaxies. Tomorrow."


def test_fetch_apod_hoy_se_calcula_en_utc(monkeypatch):
    class _Clock(datetime):
        @classmethod
        def now(cls, tz):
            assert tz is timezone.utc
            return cls(2026, 10, 18, 1, 0, tzinfo=tz)

    seen = []
    monkeypatch.setattr(apod_mod, "datetime", _Clock)
    monkeypatch.setattr(requests, "get", lambda url, **kwargs:
                        (seen.append(url), _FakeResponse(_WP_APOD))[1])
    assert fetch_apod("") is not None
    assert seen == ["https://science.nasa.gov/wp-json/wp/v2/apod-basic/261018"]


@pytest.mark.parametrize("date", ["2026-02-30", "20261018", "1995-06-15", "2999-01-01", "", 7])
def test_fetch_apod_fecha_invalida_no_consulta_red(monkeypatch, date):
    def _unexpected(*args, **kwargs):
        pytest.fail("Una fecha inválida no debe consultar NASA")

    monkeypatch.setattr(requests, "get", _unexpected)
    assert fetch_apod("", date=date) is None


@pytest.mark.parametrize("changes", [
    {"date": "2026-10-17"}, {"date": "2026-10-32"}, {"title": "NASA Science"},
    {"title": " <b>NASA Science</b> "}, {"title": "<br>"}, {"explanation": " "},
    {"title": ["malformado"]}, {"explanation": {}}, {"media_type": "other"},
    {"media_type": "image", "hdurl": None},
    {"media_type": "image", "hdurl": "https://science.nasa.gov/wp-content/themes/nasa-child/assets/images/nasa-logo@2x.png"},
])
def test_fetch_apod_rechaza_respuestas_que_antes_contaminaban_el_sidecar(monkeypatch, changes):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs:
                        _FakeResponse({**_WP_APOD, **changes}))
    assert fetch_apod("", date="2026-10-18") is None


@pytest.mark.parametrize("payload", [[], None, "NASA Science", {}])
def test_fetch_apod_json_inesperado(monkeypatch, payload):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: _FakeResponse(payload))
    assert fetch_apod("", date="2026-10-18") is None


def test_fetch_apod_json_invalido_se_omite_sin_reintento(monkeypatch):
    response = _FakeResponse(None)
    def _invalid_json():
        raise requests.exceptions.JSONDecodeError("invalid JSON", "x", 0)
    response.json = _invalid_json
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(apod_mod.time, "sleep", lambda *args: pytest.fail("No debe reintentar JSON inválido"))
    assert fetch_apod("", date="2026-10-18") is None


def test_fetch_apod_iframe_se_normaliza_como_video(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs:
                        _FakeResponse({**_WP_APOD, "media_type": "iframe"}))
    assert fetch_apod("", date="2026-10-18")["media_type"] == "video"


@pytest.mark.parametrize("media_type", ["image", "video"])
def test_tarjeta_wordpress_usa_asset_y_permalink(media_type):
    apod = {**_WP_APOD, "media_type": media_type}
    payload = build_apod_i18n(apod, translate=False)
    card = build_apod_card(apod, payload)
    assert card.image == apod["hdurl"]
    assert card.url == apod["permalink"]


@pytest.mark.parametrize("image", [
    "https://science.nasa.gov/image-article/apod-wrong/",
    "https://apod.nasa.gov/apod/ap261018.html",
    "javascript:alert(1)", "https://science.nasa.gov/", "https://science.nasa.gov/wp-json/wp/v2/apod-basic/261018",
    "https://science.nasa.gov/nasa-logo.png", {"url": "malformado"},
])
def test_tarjeta_no_muestra_paginas_o_logos_como_imagen(image):
    apod = {**_WP_APOD, "hdurl": image}
    assert build_apod_card(apod, build_apod_i18n(apod, translate=False)).image is None


def test_tarjeta_normaliza_url_relativa_al_protocolo_y_entidades():
    apod = {**_WP_APOD, "hdurl": "//assets.science.nasa.gov/image.jpg?w=960&amp;h=640"}
    assert build_apod_card(apod, build_apod_i18n(apod, translate=False)).image == (
        "https://assets.science.nasa.gov/image.jpg?w=960&h=640")


def test_build_apod_i18n_limpia_original_y_texto_enviado_al_traductor(monkeypatch):
    seen = []
    monkeypatch.setattr(apod_mod, "load_cache", lambda: {})
    monkeypatch.setattr(apod_mod, "save_cache", lambda cache: None)
    def _translate(cards, lang, cache, tracker=None):
        seen.append(cards[0])
        return {}
    monkeypatch.setattr(apod_mod, "translate_cards", _translate)
    payload = build_apod_i18n(_WP_APOD, translate=True)
    assert payload["explanation"]["en"] == "Explanation: Stars & galaxies. Tomorrow."
    assert all(card["snippet"] == payload["explanation"]["en"] for card in seen)
    assert payload["schema"] == "cl.sibylla.apod_i18n.v1"


def test_cache_de_titulo_generico_se_invalida_sin_borrar_otras_traducciones(monkeypatch):
    from sibylla.translate import _split_by_cache
    bad = {"title": "NASA Science", "snippet": "viejo", "src_title": "NASA Science"}
    cache = {lang: {"apod:2026-10-18": bad, "otra": {"title": "Conservar"}} for lang in ("es", "it")}
    monkeypatch.setattr(apod_mod, "load_cache", lambda: cache)
    monkeypatch.setattr(apod_mod, "save_cache", lambda cache: None)
    def _translate(cards, lang, cache, tracker=None):
        hits, misses = _split_by_cache(cards, lang, cache)
        assert hits == {}
        assert len(misses) == 1
        return {cards[0]["id"]: {"title": "Pilares del Águila", "snippet": "Texto corregido"}}
    monkeypatch.setattr(apod_mod, "translate_cards", _translate)
    payload = build_apod_i18n(_WP_APOD, translate=True)
    assert payload["title"]["es"] == "Pilares del Águila"
    assert cache["es"]["otra"] == {"title": "Conservar"}


def test_reparar_fecha_solo_reemplaza_su_archivo_historico(monkeypatch, tmp_path):
    _patch_sidecar_deps(monkeypatch, tmp_path)
    today_path = write_apod_sidecar(translate=False)
    today_bytes = today_path.read_bytes()
    archive = tmp_path / "apod-i18n" / "2026-10-18.json"
    archive.write_text('{"title":{"en":"NASA Science"}}', encoding="utf-8")
    seen = []
    def _fetch(api_key, *, date):
        seen.append(date)
        return _WP_APOD
    monkeypatch.setattr(web_mod, "fetch_apod", _fetch)
    path = write_apod_sidecar(translate=False, date="2026-10-18")
    assert path == archive
    assert json.loads(path.read_text(encoding="utf-8"))["title"]["en"] == _WP_APOD["title"]
    assert today_path.read_bytes() == today_bytes
    assert seen == ["2026-10-18"]


def test_reparacion_fallida_conserva_archivo_existente(monkeypatch, tmp_path):
    _patch_sidecar_deps(monkeypatch, tmp_path)
    write_apod_sidecar(translate=False)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*.json")}
    monkeypatch.setattr(web_mod, "fetch_apod", lambda *args, **kwargs: None)
    assert write_apod_sidecar(translate=False, date="2026-06-30") is None
    assert {p: p.read_bytes() for p in tmp_path.rglob("*.json")} == before


def test_cli_apod_date_solicita_solo_archivo_historico(monkeypatch, tmp_path):
    from sibylla.cli import main
    seen = []
    def _write(**kwargs):
        seen.append(kwargs)
        return tmp_path / "2026-10-18.json"
    monkeypatch.setattr(web_mod, "write_apod_sidecar", _write)
    assert main(["--apod-only", "--apod-date", "2026-10-18", "--translate", "off"]) == 0
    assert seen == [{"translate": False, "date": "2026-10-18"}]


@pytest.mark.parametrize("args", [
    ["--apod-date", "2026-10-18"],
    ["--apod-only", "--apod-date", "../escape"],
    ["--apod-only", "--apod-date", "20261018"],
    ["--apod-only", "--apod-date", "2999-01-01"],
    ["--apod-only", "--apod-date", "2026-10-18", "--dashboard"],
])
def test_cli_rechaza_reparacion_invalida(args):
    from sibylla.cli import main
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
