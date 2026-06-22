"""Selección editorial de la sección Nacional (Chile): embudo de dos etapas.

A diferencia de IA/medicina, la "relevancia" nacional NO es coincidencia temática
(todo lo que publican estos medios ya es noticia nacional) sino VALOR NOTICIOSO.
Por eso la selección combina:

  1. Pre-filtro heurístico (gratis): frescura + corroboración cruzada + posición
     en feed, ya horneados en `pipeline._score`. Da un shortlist de candidatos.
  2. Juez LLM (build-time): elige y ordena los N finales con una rúbrica que
     valora impacto/interés público SIN castigar la investigación exclusiva
     (la denuncia que tiene un solo medio no debe perder frente a la nota que
     todos repiten).
  3. Cuota: tope por medio + mínimo de tarjetas regionales.

Degrada con elegancia: sin LLM (o ante error/parseo fallido) cae al top-N
heurístico con la misma cuota. Nunca rompe el build.
"""
from __future__ import annotations

import json
import logging
import re

from .i18n import load_translations, t
from .llm import LLMError, get_provider
from .models import NewsItem
from .pipeline import _score

log = logging.getLogger("sibylla")

NACIONAL_TOPIC = "nacional"

# Parámetros de la sección (alineados con la decisión del producto).
N_CARDS = 6
MIN_REGIONAL = 2
MAX_PER_OUTLET = 2
SHORTLIST_N = 30


def is_nacional(it: NewsItem) -> bool:
    """True si el ítem pertenece a la sección Nacional (tema primario 'nacional')."""
    return bool(it.topics) and it.topics[0] == NACIONAL_TOPIC


def _outlet_key(it: NewsItem) -> str:
    """Clave de 'medio' para el tope de diversidad. Los ítems de
    google_news_nacional comparten source_id, así que se distinguen por el
    publisher real que rescata fetch_googlenews en el source_name/extra."""
    pub = (it.extra.get("publisher") or "").strip().lower()
    return pub or it.source_id


def _is_regional(it: NewsItem) -> bool:
    return it.extra.get("scope") == "regional"


def _parse_ids(text: str, n_max: int) -> list[int]:
    """Extrae el array JSON de ids (números) de la respuesta del juez. Robusto a
    fences markdown y a ids envueltos en objetos {"id": k}. Devuelve [] si falla."""
    m = re.search(r"\[.*\]", text.strip(), re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except (ValueError, TypeError):
        return []
    out: list[int] = []
    for x in data:
        if isinstance(x, dict):
            x = x.get("id")
        try:
            i = int(x)
        except (ValueError, TypeError):
            continue
        if 0 <= i < n_max and i not in out:
            out.append(i)
    return out


def _judge(shortlist: list[NewsItem], n: int) -> tuple[list[NewsItem] | None, list[dict]]:
    """Pide al LLM que elija y ordene los `n` titulares más relevantes.

    Los prompts viven en locales/es.json (la selección es sobre contenido en
    español y solo devuelve ids, así que el idioma de salida del sitio no influye).
    Retorna (elegidos | None, llm_calls). None si no hay LLM o no se pudo parsear.
    """
    provider = get_provider()
    if provider is None:
        return None, []

    tr = load_translations("es")
    payload = [{
        "id": i,
        "title": it.title,
        "source": it.source_name,
        "scope": it.extra.get("scope", ""),
        "date": it.published.strftime("%Y-%m-%d") if it.published else "",
        "snippet": (it.summary or "")[:200],
    } for i, it in enumerate(shortlist)]

    system = t(tr, "nacional.system_prompt", n=n)
    user = t(tr, "nacional.user_prompt", n=n,
             items_json=json.dumps(payload, ensure_ascii=False))

    log.info("Seleccionando Nacional con %s (%s)…", provider.name, provider.model)
    # Holgado a propósito: los modelos de razonamiento consumen tokens de salida
    # "pensando" antes de emitir el array; con un tope bajo el contenido visible
    # sale vacío. 2000 deja margen para el razonamiento + el array de ids.
    resp = provider.complete(system, user, max_tokens=2000, temperature=0.2)
    usg = resp.usage or {}
    calls = [{
        "purpose": "nacional_judge",
        "model": f"{provider.name}:{provider.model}",
        "input": usg.get("input", 0),
        "output": usg.get("output", 0),
    }]
    ids = _parse_ids(resp.text, len(shortlist))
    if not ids:
        log.warning("Juez LLM nacional: respuesta sin ids válidos; uso heurística.")
        return None, calls
    return [shortlist[i] for i in ids], calls


def _apply_quota(ordered: list[NewsItem], n: int,
                 min_regional: int, max_per_outlet: int) -> list[NewsItem]:
    """Elige `n` ítems de `ordered` (ya en orden de prioridad) respetando el tope
    por medio, y garantiza al menos `min_regional` regionales: si faltan, sustituye
    los nacionales de MENOR prioridad por los mejores regionales disponibles.
    Preserva el orden de prioridad en el resultado."""
    order_index = {id(it): i for i, it in enumerate(ordered)}
    selected: list[NewsItem] = []
    counts: dict[str, int] = {}
    for it in ordered:
        if len(selected) >= n:
            break
        k = _outlet_key(it)
        if counts.get(k, 0) >= max_per_outlet:
            continue
        selected.append(it)
        counts[k] = counts.get(k, 0) + 1

    n_reg = sum(1 for it in selected if _is_regional(it))
    if n_reg < min_regional:
        chosen_ids = {id(x) for x in selected}
        pool = [it for it in ordered if _is_regional(it) and id(it) not in chosen_ids]
        for cand in pool:
            if n_reg >= min_regional:
                break
            k = _outlet_key(cand)
            if counts.get(k, 0) >= max_per_outlet:
                continue
            victim_idx = next((i for i in range(len(selected) - 1, -1, -1)
                               if not _is_regional(selected[i])), None)
            if victim_idx is None:
                break  # ya no quedan nacionales que sacrificar
            victim = selected.pop(victim_idx)
            counts[_outlet_key(victim)] -= 1
            selected.append(cand)
            counts[k] = counts.get(k, 0) + 1
            n_reg += 1

    selected.sort(key=lambda it: order_index[id(it)])
    return selected[:n]


def select_nacional(items: list[NewsItem], *, n: int = N_CARDS,
                    min_regional: int = MIN_REGIONAL,
                    max_per_outlet: int = MAX_PER_OUTLET,
                    shortlist_n: int = SHORTLIST_N) -> tuple[list[NewsItem], list[dict]]:
    """Reordena `items` dejando al frente los `n` elegidos de la sección Nacional.

    Devuelve (items_reordenados, llm_calls). Los ítems no nacionales y el overflow
    nacional se conservan (nada se descarta): solo se ordena para que el render de
    la web (que toma los primeros `max_por_tema` por tema) muestre los elegidos.
    """
    nac = [it for it in items if is_nacional(it)]
    if not nac:
        return items, []
    others = [it for it in items if not is_nacional(it)]

    ranked = sorted(nac, key=_score, reverse=True)
    shortlist = ranked[:shortlist_n]

    chosen: list[NewsItem] | None = None
    calls: list[dict] = []
    try:
        chosen, calls = _judge(shortlist, n)
    except LLMError as ex:
        log.warning("Juez LLM nacional no disponible (%s); uso heurística.", ex)
    except Exception as ex:  # noqa: BLE001  (nunca romper el build)
        log.warning("Juez LLM nacional falló (%s); uso heurística.", ex)

    if chosen:
        chosen_ids = {id(x) for x in chosen}
        ordered = chosen + [it for it in ranked if id(it) not in chosen_ids]
    else:
        ordered = ranked

    final = _apply_quota(ordered, n, min_regional, max_per_outlet)
    final_ids = {id(x) for x in final}
    leftover_nac = [it for it in ranked if id(it) not in final_ids]

    log.info("Nacional: %d candidatos -> %d elegidos (%d regionales)%s",
             len(nac), len(final), sum(1 for it in final if _is_regional(it)),
             "" if chosen else " [heurística]")
    return final + others + leftover_nac, calls
