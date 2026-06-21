"""Orquesta el ingestor: seleccionar fuentes -> fetch -> dedupe -> rank."""
from __future__ import annotations

import logging

from .config import load_env, load_registry, index_by_id
from .fetchers import TOPIC_CONFIG, fetch_source
from .models import NewsItem

log = logging.getLogger("sibylla")

# Conjunto gratis (sin tocar X): APIs/agregadores por consulta + medios por RSS.
DEFAULT_FREE_SOURCES = [
    # APIs y agregadores (búsqueda por tema)
    "arxiv_api", "pubmed_eutils", "google_news_rss", "hacker_news",
    # Medios por RSS directo (clasificados por relevancia de tema)
    "nature_news", "bbc_science", "mit_tech_review", "phys_org", "sciencedaily",
    "the_conversation", "techcrunch", "scientific_american", "quanta",
    "ieee_spectrum", "agencia_sinc",
]

# Peso por confiabilidad. Tier 1 (peer-review/oficial) pesa más que un agregador.
TIER_WEIGHT = {1: 1.0, 2: 0.7, 3: 0.45}

# Vida media de la frescura, en horas (a las 48 h una noticia vale la mitad).
RECENCY_HALFLIFE_H = 48.0


def dedupe(items: list[NewsItem]) -> list[NewsItem]:
    """Une duplicados por URL canónica / título. Conserva el de mayor tier
    (número más bajo) y fusiona los temas."""
    seen: dict[str, NewsItem] = {}
    for it in items:
        key = it.dedup_key
        cur = seen.get(key)
        if cur is None:
            seen[key] = it
            continue
        merged_topics = sorted(set(cur.topics) | set(it.topics))
        winner = it if it.tier < cur.tier else cur
        winner.topics = merged_topics
        seen[key] = winner
    return list(seen.values())


def _score(it: NewsItem) -> float:
    weight = TIER_WEIGHT.get(it.tier, 0.4)
    if it.published is not None:
        recency = 0.5 ** (it.age_hours / RECENCY_HALFLIFE_H)
    else:
        recency = 0.25  # penaliza ítems sin fecha
    bonus = 0.0
    points = it.extra.get("points")
    if points:
        bonus = min(0.15, points / 1500.0)  # pequeño empujón por tracción en HN
    return 0.6 * weight + 0.4 * recency + bonus


def rank(items: list[NewsItem]) -> list[NewsItem]:
    return sorted(items, key=_score, reverse=True)


# Máximo de ítems por (fuente, tema) en la zona alta, para que una sola fuente
# (p. ej. arXiv con muchos preprints) no tape al resto. El sobrante va al final.
MAX_PER_SOURCE_TOPIC = 3


def diversify(items: list[NewsItem], max_per_source: int = MAX_PER_SOURCE_TOPIC) -> list[NewsItem]:
    """Limita cuántos ítems aporta una misma fuente dentro de un tema (el resto, al final)."""
    kept: list[NewsItem] = []
    overflow: list[NewsItem] = []
    counts: dict[tuple[str, str], int] = {}
    for it in items:  # ya viene ordenado por score desc
        key = (it.source_id, it.topics[0] if it.topics else "")
        if counts.get(key, 0) < max_per_source:
            counts[key] = counts.get(key, 0) + 1
            kept.append(it)
        else:
            overflow.append(it)
    return kept + overflow


def run_pipeline(topics: list[str], sources_filter: list[str] | None = None,
                 limit: int = 10) -> tuple[list[NewsItem], dict, int]:
    """Retorna (items rankeados, meta del registro, conteo crudo antes de deduplicar)."""
    load_env()
    meta, all_sources = load_registry()
    by_id = index_by_id(all_sources)

    wanted_ids = sources_filter or DEFAULT_FREE_SOURCES
    selected = [by_id[i] for i in wanted_ids if i in by_id]
    missing = [i for i in wanted_ids if i not in by_id]
    if missing:
        log.warning("IDs de fuente no encontrados en el registro: %s", missing)

    topic_cfgs = [(t, TOPIC_CONFIG[t]) for t in topics if t in TOPIC_CONFIG]
    unknown = [t for t in topics if t not in TOPIC_CONFIG]
    if unknown:
        log.warning("Temas sin configurar (se omiten): %s", unknown)
    if not topic_cfgs:
        log.error("Ningún tema válido. Disponibles: %s", ", ".join(TOPIC_CONFIG))
        return [], meta, 0

    log.info("Fuentes: %s | Temas: %s", [s.id for s in selected], [t for t, _ in topic_cfgs])
    raw: list[NewsItem] = []
    for s in selected:
        raw.extend(fetch_source(s, topic_cfgs, limit))

    deduped = dedupe(raw)
    ranked = diversify(rank(deduped))
    log.info("Total: %d crudos -> %d tras deduplicar", len(raw), len(deduped))
    return ranked, meta, len(raw)
