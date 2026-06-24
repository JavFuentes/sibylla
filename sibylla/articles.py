"""Extracción del cuerpo de artículos de prensa para los resúmenes con LLM.

Los fetchers solo traen título + snippet (el resumen corto del feed). Para que
el LLM redacte un resumen más rico hace falta el texto del artículo. Esto es
frágil por naturaleza (paywalls, sitios JS, bloqueo de bots, URLs de Google
News opacas): por eso todo fallo se traduce en None y el llamador degrada con
elegancia (la tarjeta simplemente no muestra botón de resumen).

Los papers (arXiv/PubMed) NO pasan por aquí: su abstract ya viene en el feed y
se usa directamente (robusto). Solo la prensa se fetchea.

Cache en data/articles.json (por URL canónica) para no re-descargar entre
corridas lo que ya se bajó.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from .config import ROOT
from .fetchers import _get, resolve_google_news_url
from .models import NewsItem, canonicalize_url

log = logging.getLogger("sibylla")

_CACHE_PATH = ROOT / "data" / "articles.json"

# Texto más corto que esto = no es cuerpo real (menú, error, página de login...).
_MIN_CHARS = 200
# Tope de caracteres que se envían al LLM: los artículos-front-load, con los
# primeros ~5000 chars suele alcanzar para un resumen fiel. Evita lotes enormes.
_MAX_CHARS = 5000


def _load_cache() -> dict:
    if not _CACHE_PATH.exists():
        return {}
    try:
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")


def _extract(html_bytes: bytes, url: str) -> Optional[str]:
    """Extrae el texto principal del HTML con trafilatura. None si no hay cuerpo."""
    try:
        import trafilatura
    except ImportError:  # pragma: no cover
        log.warning("trafilatura no instalado; no se pueden extraer artículos.")
        return None
    try:
        return trafilatura.extract(
            html_bytes, url=url, include_comments=False, include_tables=False,
            favor_recall=True,
        )
    except Exception as ex:  # noqa: BLE001
        log.debug("trafilatura no pudo extraer %s: %s", url, ex)
        return None


def fetch_article_text(url: str, *, timeout: int = 20) -> Optional[str]:
    """Descarga el artículo y devuelve su texto principal, o None si falla.

    Resuelve la URL de Google News (best-effort), descarga el HTML y extrae el
    cuerpo. Devuelve None ante cualquier problema (timeout, HTTP >= 400, bloqueo,
    paywall, contenido trivial). Solo se cachean los éxitos (los fallos se
    reintentan en la próxima corrida). Recorta a _MAX_CHARS para el LLM.
    """
    if not url:
        return None
    url = resolve_google_news_url(url)
    if not url or not url.lower().startswith(("http://", "https://")):
        return None
    key = canonicalize_url(url)

    cache = _load_cache()
    if key in cache:
        return cache[key]

    try:
        resp = _get(url, timeout=timeout)
        html_bytes = resp.content
    except Exception as ex:  # noqa: BLE001  (timeout, 4xx/5xx, DNS...)
        log.debug("fetch artículo falló (%s): %s", url, ex)
        return None

    text = _extract(html_bytes, url)
    if not text or len(text) < _MIN_CHARS:
        return None
    text = text[:_MAX_CHARS]

    cache[key] = text
    _save_cache(cache)
    return text


def card_content(it: NewsItem) -> Optional[str]:
    """Texto fuente para el resumen de una tarjeta.

    - Papers (arXiv/PubMed): el abstract del feed (robusto, sin red).
    - Prensa (y PubMed sin abstract): el cuerpo del artículo fetchado (frágil).
    Devuelve None si no hay nada aprovechable.
    """
    kind = (it.extra.get("kind") or "").lower()
    is_paper = kind in ("preprint", "paper") or it.source_id in ("arxiv_api", "pubmed_eutils")
    if is_paper and it.summary:
        return it.summary[:_MAX_CHARS]
    return fetch_article_text(it.url)
