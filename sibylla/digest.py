"""Renderiza los ítems rankeados en un resumen Markdown con enlaces a la fuente.

NOTA: esta versión arma el resumen de forma determinista (sin LLM). El gancho
`summarize_items()` queda listo para enchufar más adelante una síntesis con
Claude (la "procesamiento de la información" que se verá después).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .models import NewsItem

TIER_LABEL = {1: "T1", 2: "T2", 3: "T3"}


def _meta_line(it: NewsItem) -> str:
    date = f"{it.published:%Y-%m-%d}" if it.published else "s/f"
    bits = [it.source_name, date, TIER_LABEL.get(it.tier, f"T{it.tier}")]
    if it.extra.get("points"):
        bits.append(f"▲{it.extra['points']} HN")
    return " · ".join(b for b in bits if b)


def render_digest(items: list[NewsItem], topics: list[str], meta: dict,
                  max_per_topic: int = 12) -> str:
    now = datetime.now(timezone.utc)
    out: list[str] = []
    out.append(f"# Sibylla — Resumen ({', '.join(topics)})")
    out.append(f"_Generado {now:%Y-%m-%d %H:%M UTC} · {len(items)} ítems tras deduplicar_")

    by_topic: dict[str, list[NewsItem]] = {}
    for it in items:
        key = it.topics[0] if it.topics else "otros"
        by_topic.setdefault(key, []).append(it)

    for topic in topics:
        group = by_topic.get(topic, [])[:max_per_topic]
        if not group:
            continue
        out.append(f"\n## {topic}\n")
        for it in group:
            out.append(f"- **[{it.title}]({it.url})**  ")
            out.append(f"  <sub>{_meta_line(it)}</sub>")
            if it.summary:
                snippet = it.summary[:240] + ("…" if len(it.summary) > 240 else "")
                out.append(f"  {snippet}")

    out.append("\n---")
    out.append(
        "<sub>Tiers: **T1** primaria/peer-review · **T2** periodismo · "
        "**T3** agregador/discusión. Cada ítem enlaza a su fuente original.</sub>"
    )
    return "\n".join(out)


def summarize_items(items: list[NewsItem], topics: list[str]) -> str:
    """HOOK para la síntesis con LLM (futuro).

    Aquí se llamaría a Claude para: agrupar historias relacionadas, redactar un
    resumen en el idioma del usuario y marcar el nivel de confianza. De momento
    delega en el render determinista.
    """
    raise NotImplementedError("Síntesis con LLM pendiente (siguiente fase).")
