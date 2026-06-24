"""Generación de resúmenes en español para las tarjetas renderizadas.

Para cada tarjeta visible se obtiene el texto fuente (abstract para papers,
cuerpo del artículo para prensa vía ``articles.card_content``) y el LLM redacta
un resumen de ~1 párrafo en español. El resumen se muestra tras el botón
"Resumen" de la tarjeta (acordeón inline).

Degradación elegante: sin LLM, si falla el fetch del artículo o si el modelo no
devuelve resumen para un ítem, ese ítem simplemente queda sin resumen (y la
tarjeta no muestra el botón). Nunca rompe el build.

Cache en ``data/resumenes.json`` por ``dedup_key`` (con ``src_title`` para
invalidar si la fuente cambia el título): evita re-generar lo ya hecho entre
corridas, igual que el cache de traducciones.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .articles import card_content
from .config import ROOT
from .i18n import load_translations, t
from .llm import LLMError, get_provider
from .models import NewsItem

log = logging.getLogger("sibylla")

CACHE_PATH = ROOT / "data" / "resumenes.json"

# 1 intento inicial + 1 reintento de los ids que el modelo omita. Lo que siga
# faltando queda sin resumen y se reintenta en la próxima corrida.
_MAX_ATTEMPTS = 2

# Ítems por llamada. El texto fuente puede ser largo (artículo), así que el
# lote es pequeño para que el JSON de salida nunca pegue el tope de tokens.
_CHUNK_SIZE = 4


# ---------------------------------------------------------------------------
# Cache  { dedup_key: {"resumen", "src_title"} }
# ---------------------------------------------------------------------------
def load_cache(path: Path = CACHE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)


def _split_by_cache(cards: list[dict], cache: dict) -> tuple[dict, list[dict]]:
    """Separa en (hits, misses). Hit = resumen cacheado y vigente (mismo src_title)."""
    hits: dict[str, str] = {}
    misses: list[dict] = []
    for c in cards:
        entry = cache.get(c["id"])
        if entry and entry.get("src_title") == c["title"]:
            hits[c["id"]] = entry["resumen"]
        else:
            misses.append(c)
    return hits, misses


# ---------------------------------------------------------------------------
# Parseo robusto (mismo molde que translate._extract_json_array)
# ---------------------------------------------------------------------------
def _extract_json_array(raw: str):
    s = (raw or "").strip()
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


def _parse_response(raw: str, valid_ids: set[str]) -> dict[str, str]:
    data = _extract_json_array(raw)
    if not isinstance(data, list):
        return {}
    out: dict[str, str] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        resumen = row.get("resumen")
        if rid in valid_ids and isinstance(resumen, str) and resumen.strip():
            out[rid] = resumen.strip()
    return out


# ---------------------------------------------------------------------------
# Llamada al LLM (un lote por sección)
# ---------------------------------------------------------------------------
def _batch(cards: list[dict], provider, *, max_tokens: int,
           tracker: list[dict] | None = None) -> dict[str, str]:
    """Resumen en español de cada card. Devuelve {id: resumen}."""
    tr = load_translations("es")
    payload = [{"id": c["id"], "title": c["title"], "text": c["text"]} for c in cards]
    system = t(tr, "resumen.system_prompt")
    user = t(tr, "resumen.user_prompt", items_json=json.dumps(payload, ensure_ascii=False))
    log.info("Resumiendo %d tarjetas con %s (%s)…", len(cards), provider.name, provider.model)
    resp = provider.complete(system, user, max_tokens=max_tokens, temperature=0.2)
    usg = resp.usage or {}
    if tracker is not None:
        tracker.append({
            "purpose": "resumen",
            "model": f"{provider.name}:{provider.model}",
            "input": usg.get("input", 0),
            "output": usg.get("output", 0),
        })
    return _parse_response(resp.text, valid_ids={c["id"] for c in cards})


def build_resumenes(items: list[NewsItem], *,
                    tracker: list[dict] | None = None) -> dict[str, str]:
    """Resúmenes en ES de los ítems dados. Devuelve {dedup_key: resumen}.

    Solo procesa los ítems pasados (típicamente los renderizados). Obtiene el
    texto fuente de cada uno (abstract o artículo), llama al LLM en lotes y
    cachea. Sin LLM o ante error, devuelve solo los aciertos del cache.
    """
    # Construir cards: {id, title, text}. Descarta los que no tengan texto fuente.
    cards: list[dict] = []
    vistos: set[str] = set()
    for it in items:
        if it.dedup_key in vistos:
            continue
        vistos.add(it.dedup_key)
        text = card_content(it)
        if not text:
            continue  # sin texto fuente -> sin resumen (sin botón)
        cards.append({"id": it.dedup_key, "title": it.title, "text": text})

    if not cards:
        return {}

    cache = load_cache()
    hits, misses = _split_by_cache(cards, cache)
    if not misses:
        return hits

    try:
        provider = get_provider()
    except LLMError as exc:
        log.warning("Resúmenes desactivados (%s). Tarjetas sin botón de resumen.", exc)
        return hits
    if provider is None:
        return hits

    fresh: dict[str, str] = {}
    for inicio in range(0, len(misses), _CHUNK_SIZE):
        chunk = misses[inicio:inicio + _CHUNK_SIZE]
        pendientes = chunk
        for intento in range(_MAX_ATTEMPTS):
            try:
                got = _batch(pendientes, provider, max_tokens=2500, tracker=tracker)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fallo al resumir un lote (%s). Quedan sin resumen.", exc)
                break
            fresh.update(got)
            pendientes = [c for c in pendientes if c["id"] not in fresh]
            if not pendientes:
                break
            if intento + 1 < _MAX_ATTEMPTS:
                log.info("Reintentando %d tarjeta(s) sin resumen…", len(pendientes))

    # Persistir en cache con src_title para invalidar si cambia el título fuente.
    for c in misses:
        r = fresh.get(c["id"])
        if r:
            cache[c["id"]] = {"resumen": r, "src_title": c["title"]}

    save_cache(cache)
    hits.update(fresh)
    return hits
