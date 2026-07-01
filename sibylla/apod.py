"""Sidecar de traduccion es/it para el APOD (Astronomy Picture of the Day) de HOY.

Stellar-View pide el APOD directo a la API de NASA (imagen + texto en ingles,
para cualquier fecha del archivo). Este modulo publica un JSON aparte,
`apod-i18n.json`, con SOLO el titulo/explicacion de HOY traducidos: el
selector de fecha de la app cubre 30 anios de archivo, imposible de traducir
por completo, y el texto es identico para todo el mundo (tiene sentido
traducirlo una vez aqui, no una vez por dispositivo).

Reutiliza `translate.py::translate_cards` (mismo cache `translations.json`,
mismo troceo/reintento) tratando titulo+explicacion como una card mas, igual
que hace `web.py` con las noticias. Degrada con elegancia: si falla la API de
NASA o el LLM, no se escribe el archivo (o queda solo en ingles) y la app cae
al ingles de NASA para la fecha de hoy. Nunca rompe el build.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from .translate import load_cache, save_cache, translate_cards

log = logging.getLogger("sibylla")

APOD_URL = "https://api.nasa.gov/planetary/apod"
APOD_I18N_SCHEMA = "cl.sibylla.apod_i18n.v1"

# 'en' es el original de NASA (no requiere traduccion).
APOD_I18N_LANGS = ("es", "it")


def fetch_apod(api_key: str, *, timeout: int = 20) -> dict | None:
    """Descarga el APOD de HOY desde la API de NASA. None ante cualquier fallo."""
    try:
        r = requests.get(APOD_URL, params={"api_key": api_key}, timeout=timeout)
        r.raise_for_status()
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("No se pudo obtener el APOD de hoy (%s). Sidecar de traduccion omitido.", exc)
        return None
    if not isinstance(data, dict) or not data.get("title") or not data.get("explanation") or not data.get("date"):
        log.warning("Respuesta de APOD incompleta. Sidecar de traduccion omitido.")
        return None
    return data


def build_apod_i18n(apod: dict, *, translate: bool, tracker: list[dict] | None = None) -> dict:
    """Contrato publico que consume Stellar-View (`apod-i18n.json`).

    `apod` es la respuesta cruda de la API de NASA (ver `fetch_apod`). `title` y
    `explanation` son dicts que siempre traen 'en'; 'es'/'it' se agregan si la
    traduccion tuvo exito (si no, la app cae a 'en' para esos idiomas).
    """
    date = apod["date"]
    payload: dict = {
        "schema": APOD_I18N_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "date": date,
        "title": {"en": apod["title"]},
        "explanation": {"en": apod["explanation"]},
    }
    if not translate:
        return payload

    cache = load_cache()
    # Titulo+explicacion viajan como una sola card (title/snippet) para
    # traducirse en UNA llamada por idioma, igual que translate_cards ya hace
    # para las tarjetas de noticias.
    card = {"id": f"apod:{date}", "title": apod["title"], "snippet": apod["explanation"]}
    for lang in APOD_I18N_LANGS:
        got = translate_cards([card], lang, cache, tracker=tracker).get(card["id"])
        if got and got.get("title"):
            payload["title"][lang] = got["title"]
        if got and got.get("snippet"):
            payload["explanation"][lang] = got["snippet"]
    save_cache(cache)
    return payload
