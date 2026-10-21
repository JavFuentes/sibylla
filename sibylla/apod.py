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

import html
import logging
import time
from datetime import date as Date, datetime, timezone
from urllib.parse import urlsplit

import requests

from .models import NewsItem, clean_text
from .net import safe_error
from .translate import load_cache, save_cache, translate_cards

log = logging.getLogger("sibylla")

APOD_URL = "https://science.nasa.gov/wp-json/wp/v2/apod-basic"
APOD_I18N_SCHEMA = "cl.sibylla.apod_i18n.v1"
# source_id reservado para la tarjeta APOD dentro de la sección Astronomía.
# Debe estar FUERA de ASTRO_SOURCE_IDS (web.py) para que el pipeline no lo procese:
# la tarjeta se construye desde la API de NASA, no desde el fetch de fuentes RSS.
APOD_SOURCE_ID = "apod"

# 'en' es el original de NASA (no requiere traduccion).
APOD_I18N_LANGS = ("es", "it")

# Codigos que ameritan reintento (fallo transitorio del lado de NASA, no del
# request). Medido: la API de NASA devuelve 503 en ~1 de cada 5 llamadas en
# horas pico. Un 4xx que no sea de esta lista (401 key invalida, 400 fecha
# mala...) es un error permanente: reintentar no lo arregla.
_RETRY_STATUS = {408, 429, 500, 502, 503, 504}


def parse_apod_date(value: str) -> Date:
    """Fecha ISO del archivo APOD, entre su inicio y hoy (UTC)."""
    day = Date.fromisoformat(value)
    if day.isoformat() != value or not Date(1995, 6, 16) <= day <= datetime.now(timezone.utc).date():
        raise ValueError("La fecha APOD debe ser YYYY-MM-DD, entre 1995-06-16 y hoy (UTC).")
    return day


def _http_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value.strip())
    if value.startswith("//"):
        value = "https:" + value
    try:
        parts = urlsplit(value)
        if parts.scheme in ("http", "https") and parts.hostname and not parts.username:
            return value
    except ValueError:
        pass
    return None


def _image_url(value: object) -> str | None:
    """WordPress usa `url` para el permalink: nunca debe acabar en un <img>."""
    url = _http_url(value)
    if url is None:
        return None
    parts = urlsplit(url)
    path = parts.path.lower()
    if (path in ("", "/") or "nasa-logo" in path or path.endswith((".html", ".htm"))
            or (parts.hostname == "science.nasa.gov" and
                path.startswith(("/image-article/", "/wp-json/")))):
        return None
    return url


def _apod_image(apod: dict) -> str | None:
    if apod.get("media_type") == "image":
        return _image_url(apod.get("hdurl")) or _image_url(apod.get("url"))
    return _image_url(apod.get("thumbnail_url")) or _image_url(apod.get("hdurl"))


def fetch_apod(api_key: str, *, date: str | None = None,
               timeout: int = 20, attempts: int = 3) -> dict | None:
    """Descarga y normaliza el APOD de la fecha indicada (hoy UTC por defecto).

    Reintenta con backoff corto (2s, 4s) ante fallos transitorios (ver
    _RETRY_STATUS, mas timeouts/errores de conexion): el cron que llama a esto
    tiene pocas oportunidades de reintento a nivel de corrida completa (ver
    .github/workflows/regenerate-apod.yml). Un error permanente (p.ej. API key
    invalida) se abandona en el primer intento, sin reintentar en vano.
    """
    try:
        day = parse_apod_date(date if date is not None else datetime.now(timezone.utc).date().isoformat())
    except (TypeError, ValueError):
        log.warning("Fecha APOD inválida; sidecar omitido.")
        return None
    # La API WordPress ignora ?date; el identificador va en la ruta, yyMMdd.
    url = f"{APOD_URL}/{day.strftime('%y%m%d')}"
    last_err = ""
    for i in range(attempts):
        try:
            r = requests.get(url, params={"api_key": api_key}, timeout=timeout)
            r.raise_for_status()
            data = r.json()
            break
        except ValueError as exc:
            # requests.JSONDecodeError también hereda RequestException: un JSON
            # inválido debe salir aquí, antes de los reintentos de transporte.
            log.warning("No se pudo obtener el APOD de %s (%s). Sidecar omitido.",
                        day.isoformat(), safe_error(exc))
            return None
        except requests.RequestException as exc:
            last_err = safe_error(exc)
            status = getattr(exc.response, "status_code", None)
            if status is not None and status not in _RETRY_STATUS:
                log.warning("No se pudo obtener el APOD de %s (%s). Sidecar omitido.",
                            day.isoformat(), last_err)
                return None
        if i < attempts - 1:
            time.sleep(2 ** (i + 1))  # 2s, 4s
    else:
        log.warning("No se pudo obtener el APOD de %s tras %d intentos (%s). Sidecar omitido.",
                    day.isoformat(), attempts, last_err)
        return None

    if (not isinstance(data, dict) or data.get("date") != day.isoformat()
            or not isinstance(data.get("title"), str)
            or not isinstance(data.get("explanation"), str)
            or data.get("media_type") not in ("image", "video", "iframe")):
        log.warning("Respuesta de APOD incompleta o de otra fecha. Sidecar omitido.")
        return None
    data = dict(data, title=clean_text(data["title"]), explanation=clean_text(data["explanation"]))
    if data["media_type"] == "iframe":
        data["media_type"] = "video"
    if (not data["title"] or data["title"].casefold() == "nasa science" or not data["explanation"]
            or (data["media_type"] == "image" and _apod_image(data) is None)):
        log.warning("Respuesta de APOD genérica o sin contenido utilizable. Sidecar omitido.")
        return None
    return data


def build_apod_i18n(apod: dict, *, translate: bool, tracker: list[dict] | None = None) -> dict:
    """Contrato publico que consume Stellar-View (`apod-i18n.json`).

    `apod` es la respuesta normalizada de NASA (ver `fetch_apod`). `title` y
    `explanation` son dicts que siempre traen 'en'; 'es'/'it' se agregan si la
    traduccion tuvo exito (si no, la app cae a 'en' para esos idiomas).
    """
    date = apod["date"]
    title = clean_text(apod["title"])
    explanation = clean_text(apod["explanation"])
    payload: dict = {
        "schema": APOD_I18N_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "date": date,
        "title": {"en": title},
        "explanation": {"en": explanation},
    }
    if not translate:
        return payload

    cache = load_cache()
    # Titulo+explicacion viajan como una sola card (title/snippet) para
    # traducirse en UNA llamada por idioma, igual que translate_cards ya hace
    # para las tarjetas de noticias.
    card = {"id": f"apod:{date}", "title": title, "snippet": explanation}
    for lang in APOD_I18N_LANGS:
        got = translate_cards([card], lang, cache, tracker=tracker).get(card["id"])
        if got and got.get("title"):
            payload["title"][lang] = clean_text(got["title"])
        if got and got.get("snippet"):
            payload["explanation"][lang] = clean_text(got["snippet"])
    save_cache(cache)
    return payload


def build_apod_card(apod: dict, payload: dict) -> NewsItem | None:
    """Convierte el APOD del día en un NewsItem para la sección Astronomía.

    El título y snippet que muestra la web se inyectan en ES desde `payload`
    en `build_all_sites` (web.py), sin llamar al LLM de nuevo. Aquí solo se
    ponen los valores originales en inglés como base (y como fallback si falla
    la inyección). Devuelve None ante cualquier dato malformado.

    `apod`    — respuesta normalizada de NASA (ver `fetch_apod`).
    `payload` — resultado de `build_apod_i18n` (tiene title/explanation en "en").
    """
    try:
        date_obj = datetime.strptime(apod["date"], "%Y-%m-%d")
        apod_url = (_http_url(apod.get("permalink"))
                    or f"https://apod.nasa.gov/apod/ap{date_obj.strftime('%y%m%d')}.html")
        image = _apod_image(apod)
        return NewsItem(
            title=payload["title"]["en"],
            url=apod_url,
            source_id=APOD_SOURCE_ID,
            source_name="NASA APOD",
            tier=1,
            topics=["astronomia"],
            published=datetime(date_obj.year, date_obj.month, date_obj.day, tzinfo=timezone.utc),
            summary=payload["explanation"]["en"],
            image=image,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("No se pudo construir la tarjeta APOD (%s); se omite de la sección Astronomía.", exc)
        return None
