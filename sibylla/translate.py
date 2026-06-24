"""Traducción de las tarjetas de la web (título + snippet) con un LLM.

Estrategia B+A:
  - **A**: la "cáscara" de la web (menú, encabezados, apuntes) se traduce de
    forma estática vía `locales/*.json` (sin IA).
  - **B**: el CONTENIDO dinámico de cada tarjeta (título y snippet) se traduce
    aquí con el LLM configurado, en tiempo de build, y se hornea en el HTML.

Principios:
  - Solo se traducen las tarjetas RENDERIZADAS (las visibles, ≤ máx. por tema);
    nunca el overflow que no aparece. Esto ahorra tokens.
  - Cae siempre con gracia: sin LLM configurado o ante cualquier error, devuelve
    solo lo que haya en cache y las tarjetas restantes quedan en su idioma
    original (lo resuelve `web.py` con fallback por `dedup_key`). Nunca rompe el
    build.
  - Cache persistente en `data/translations.json` (ignorado por git): evita
    re-traducir ítems ya vistos entre corridas. Mantiene barata la regeneración
    periódica (automatización).

Los prompts se cargan del locale del idioma DESTINO, igual que `summarize.py`.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .config import ROOT
from .i18n import load_translations, t
from .llm import LLMError, get_provider

log = logging.getLogger("sibylla")

CACHE_PATH = ROOT / "data" / "translations.json"

# Lotes de traducción: 1 intento inicial + 1 reintento de los ids que el modelo
# no devuelva (los LLM a veces omiten elementos de un lote). Lo que siga
# faltando cae al idioma original y se reintenta en la próxima corrida.
_MAX_ATTEMPTS = 2

# Tarjetas por llamada al LLM. Mantiene el output de cada respuesta holgado
# (lejos del tope de tokens) para que el JSON nunca se trunque. Un lote único
# con todo el sitio (nacional+ia+medicina+redes ≈ 24 tarjetas) excede `max_tokens`
# y se corta a mitad del array → 0 traducciones recuperadas.
_CHUNK_SIZE = 8


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Estructura: { lang: { dedup_key: {"title", "snippet", "src_title"} } }
# `src_title` permite invalidar una traducción si la fuente cambia el título
# para la misma URL (mismo dedup_key, contenido distinto).
def load_cache(path: Path = CACHE_PATH) -> dict:
    """Carga el cache de traducciones. {} si no existe o está corrupto."""
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict, path: Path = CACHE_PATH) -> None:
    """Persiste el cache de traducciones (crea data/ si hace falta)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=0, sort_keys=True)


def _split_by_cache(cards: list[dict], lang: str, cache: dict) -> tuple[dict, list[dict]]:
    """Separa las cards en (hits, misses).

    hits  -> {id: {title, snippet}} ya cacheados y VIGENTES (mismo src_title).
    misses-> lista de cards que hay que traducir (no cacheadas o desactualizadas).
    """
    lang_cache = cache.get(lang, {})
    hits: dict[str, dict] = {}
    misses: list[dict] = []
    for c in cards:
        entry = lang_cache.get(c["id"])
        if entry and entry.get("src_title") == c["title"]:
            hits[c["id"]] = {"title": entry["title"], "snippet": entry["snippet"]}
        else:
            misses.append(c)
    return hits, misses


# ---------------------------------------------------------------------------
# Parseo robusto de la respuesta del LLM
# ---------------------------------------------------------------------------
def _extract_json_array(raw: str) -> Any:
    """Extrae el primer array JSON del texto (tolerante a ``` y prosa). None si falla."""
    s = (raw or "").strip()
    start = s.find("[")
    end = s.rfind("]")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        return json.loads(s[start:end + 1])
    except json.JSONDecodeError:
        return None


def _parse_response(raw: str, valid_ids: set[str]) -> dict[str, dict]:
    """Convierte la respuesta del LLM en {id: {title, snippet}}.

    Ignora filas malformadas o con id desconocido. Devuelve {} si no parsea.
    """
    data = _extract_json_array(raw)
    if not isinstance(data, list):
        return {}
    out: dict[str, dict] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        title = row.get("title")
        if rid in valid_ids and isinstance(title, str) and title.strip():
            out[rid] = {
                "title": title.strip(),
                "snippet": (row.get("snippet") or "").strip(),
            }
    return out


# ---------------------------------------------------------------------------
# Llamada al LLM (una por idioma; batch de todas las cards faltantes)
# ---------------------------------------------------------------------------
def _translate_batch(cards: list[dict], lang: str, provider, *,
                     max_tokens: int, tracker: list[dict] | None = None) -> tuple[dict[str, dict], bool]:
    """Traduce en una sola llamada title+snippet de cada card a `lang`.

    Devuelve ``(traducciones, hit_cap)``. ``hit_cap`` es True cuando el output
    pegó el tope de ``max_tokens``: señal de que el JSON quedó cortado y conviene
    NO reintentar el mismo lote (se truncaría igual).

    Si se pasa un `tracker` (lista mutable), apendea {purpose, model, input, output}
    con el consumo de tokens de esta llamada.
    """
    tr = load_translations(lang)
    lang_name = t(tr, "summarize.lang_name")  # reutiliza el nombre del idioma del locale
    payload = [{"id": c["id"], "title": c["title"], "snippet": c["snippet"]} for c in cards]

    system = t(tr, "translate.system_prompt", lang=lang_name)
    user = t(tr, "translate.user_prompt",
             lang=lang_name,
             items_json=json.dumps(payload, ensure_ascii=False))

    log.info("Traduciendo %d tarjetas a %s con %s (%s)…",
             len(cards), lang, provider.name, provider.model)
    resp = provider.complete(system, user, max_tokens=max_tokens, temperature=0.2)
    usg = resp.usage or {}
    if tracker is not None:
        tracker.append({
            "purpose": f"translate_{lang}",
            "model": f"{provider.name}:{provider.model}",
            "input": usg.get("input", 0),
            "output": usg.get("output", 0),
        })
    parsed = _parse_response(resp.text, valid_ids={c["id"] for c in cards})
    # Truncamiento por longitud: el output llegó al tope -> JSON a medias.
    hit_cap = max_tokens > 0 and usg.get("output", 0) >= max_tokens
    return parsed, hit_cap


def translate_cards(cards: list[dict], lang: str, cache: dict, *,
                    max_tokens: int = 6000,
                    tracker: list[dict] | None = None) -> dict[str, dict]:
    """Traduce las cards a `lang`. Devuelve {id: {title, snippet}}.

    `cards` son dicts {"id", "title", "snippet"} (id = dedup_key del NewsItem).
    Usa y ACTUALIZA `cache` in place. Sin LLM configurado o ante error, devuelve
    solo los aciertos del cache (el resto cae al original aguas arriba).

    Trocea las misses en lotes de ``_CHUNK_SIZE`` y traduce cada lote por separado
    (con su propio reintento): así cada respuesta queda lejos del tope de tokens y
    el JSON nunca se trunca. Si un lote truncase aún así (``hit_cap``) no se
    reintenta (se cortaría igual) y solo esas tarjetas caen al idioma original.

    Si el modelo omite ítems de un lote (no truncamiento), reintenta UNA vez solo
    los que falten (ver ``_MAX_ATTEMPTS``); lo que siga faltando cae al original.

    Si se pasa un `tracker` (lista mutable), se apendea el uso de tokens de
    cada llamada exitosa al LLM.
    """
    if not cards:
        return {}

    hits, misses = _split_by_cache(cards, lang, cache)
    if not misses:
        return hits

    try:
        provider = get_provider()
    except LLMError as exc:
        log.warning("Traducción desactivada (%s). Tarjetas en idioma original.", exc)
        return hits
    if provider is None:
        return hits

    fresh: dict[str, dict] = {}
    # Trocear en lotes pequeños para que el output nunca pegue el tope de tokens.
    # El reintento opera POR LOTE: un lote problemático no condena a los demás.
    for inicio in range(0, len(misses), _CHUNK_SIZE):
        chunk = misses[inicio:inicio + _CHUNK_SIZE]
        pendientes = chunk
        for intento in range(_MAX_ATTEMPTS):
            try:
                got, hit_cap = _translate_batch(pendientes, lang, provider,
                                                max_tokens=max_tokens, tracker=tracker)
            except Exception as exc:  # noqa: BLE001
                log.warning("Fallo al traducir a %s (%s). Las restantes quedan en idioma original.", lang, exc)
                break
            fresh.update(got)
            pendientes = [c for c in pendientes if c["id"] not in fresh]
            if not pendientes:
                break
            # Si el lote truncó, reintentarlo no ayuda (volverá a cortarse).
            if hit_cap:
                log.warning("Lote de traducción a %s truncado (output=%d tokens, %d tarjetas); "
                            "%d quedan en idioma original.", lang, max_tokens, len(chunk), len(pendientes))
                break
            if intento + 1 < _MAX_ATTEMPTS:
                log.info("Reintentando %d tarjeta(s) que %s no devolvió para %s…",
                         len(pendientes), provider.name, lang)

    # Persistir las nuevas traducciones en el cache (con src_title para invalidar).
    lang_cache = cache.setdefault(lang, {})
    for c in misses:
        got = fresh.get(c["id"])
        if got:
            lang_cache[c["id"]] = {
                "title": got["title"],
                "snippet": got["snippet"],
                "src_title": c["title"],
            }

    hits.update(fresh)
    return hits
