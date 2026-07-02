"""Sidecar de traduccion es/it para el APOD (Astronomy Picture of the Day).

Stellar-View pide el APOD directo a la API de NASA (imagen + texto en ingles,
para cualquier fecha del archivo). Este modulo publica un JSON aparte,
`apod-i18n.json`, con el titulo/explicacion de HOY traducidos: el selector de
fecha de la app cubre 30 anios de archivo, imposible de traducir por completo
retroactivamente, y el texto es identico para todo el mundo (tiene sentido
traducirlo una vez aqui, no una vez por dispositivo). Cada corrida ademas deja
una copia inmutable en `apod-i18n/<fecha>.json` (ver `web.py::write_apod_sidecar`),
asi se va acumulando un archivo historico de traducciones desde que este
mecanismo empezo a correr; los dias anteriores a eso caen al ingles de NASA.

Reutiliza `translate.py::translate_cards` (mismo cache `translations.json`,
mismo troceo/reintento) tratando titulo+explicacion como una card mas, igual
que hace `web.py` con las noticias. Degrada con elegancia: si falla la API de
NASA o el LLM, no se escribe el archivo (o queda solo en ingles) y la app cae
al ingles de NASA para la fecha de hoy. Nunca rompe el build.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import requests

from .net import safe_error
from .translate import load_cache, save_cache, translate_cards

log = logging.getLogger("sibylla")

APOD_URL = "https://api.nasa.gov/planetary/apod"
APOD_I18N_SCHEMA = "cl.sibylla.apod_i18n.v1"

# 'en' es el original de NASA (no requiere traduccion).
APOD_I18N_LANGS = ("es", "it")

# Codigos que ameritan reintento (fallo transitorio del lado de NASA, no del
# request). Medido: la API de NASA devuelve 503 en ~1 de cada 5 llamadas en
# horas pico. Un 4xx que no sea de esta lista (401 key invalida, 400 fecha
# mala...) es un error permanente: reintentar no lo arregla.
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def fetch_apod(api_key: str, *, timeout: int = 20, attempts: int = 3) -> dict | None:
    """Descarga el APOD de HOY desde la API de NASA. None ante cualquier fallo.

    Reintenta con backoff corto (2s, 4s) ante fallos transitorios (ver
    _RETRY_STATUS, mas timeouts/errores de conexion): el cron que llama a esto
    tiene pocas oportunidades de reintento a nivel de corrida completa (ver
    .github/workflows/regenerate-apod.yml). Un error permanente (p.ej. API key
    invalida) se abandona en el primer intento, sin reintentar en vano.
    """
    last_err = ""
    for i in range(attempts):
        try:
            r = requests.get(APOD_URL, params={"api_key": api_key}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            break
        except requests.RequestException as exc:
            last_err = safe_error(exc)
            status = getattr(exc.response, "status_code", None)
            if status is not None and status not in _RETRY_STATUS:
                log.warning("No se pudo obtener el APOD de hoy (%s). Sidecar de traduccion omitido.", last_err)
                return None
        except ValueError as exc:
            log.warning("No se pudo obtener el APOD de hoy (%s). Sidecar de traduccion omitido.", safe_error(exc))
            return None
        if i < attempts - 1:
            time.sleep(2 ** (i + 1))  # 2s, 4s
    else:
        log.warning("No se pudo obtener el APOD de hoy tras %d intentos (%s). Sidecar de traduccion omitido.",
                    attempts, last_err)
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
