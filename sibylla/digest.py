"""Render determinista de los ítems rankeados a Markdown con enlaces a la fuente.

Usa el módulo i18n para generar el resumen en el idioma pedido.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .i18n import load_translations, t
from .models import NewsItem

TIER_LABEL = {1: "T1", 2: "T2", 3: "T3"}


def _meta_line(it: NewsItem, no_date: str) -> str:
    date = f"{it.published:%Y-%m-%d}" if it.published else no_date
    bits = [it.source_name, date, TIER_LABEL.get(it.tier, f"T{it.tier}")]
    if it.extra.get("points"):
        bits.append(f"▲{it.extra['points']} HN")
    return " · ".join(b for b in bits if b)


def render_digest(items: list[NewsItem], topics: list[str], meta: dict,
                  lang: str = "es", max_per_topic: int = 12) -> str:
    tr = load_translations(lang)
    now = datetime.now(timezone.utc)
    out: list[str] = []
    out.append(t(tr, "digest.title", topics=", ".join(topics)))
    out.append(t(tr, "digest.generated", date=f"{now:%Y-%m-%d %H:%M UTC}", count=len(items)))

    by_topic: dict[str, list[NewsItem]] = {}
    for it in items:
        key = it.topics[0] if it.topics else "otros"
        by_topic.setdefault(key, []).append(it)

    no_date = t(tr, "digest.no_date")
    also_in = t(tr, "digest.also_in")
    for topic in topics:
        group = by_topic.get(topic, [])[:max_per_topic]
        if not group:
            continue
        out.append(f"\n## {topic}\n")
        for it in group:
            out.append(f"- **[{it.title}]({it.url})**  ")
            out.append(f"  <sub>{_meta_line(it, no_date)}</sub>")
            if it.related:
                medios = ", ".join(r["source_name"] for r in it.related)
                out.append(f"  <sub>{also_in}: {medios}</sub>")
            if it.summary:
                snippet = it.summary[:240] + ("…" if len(it.summary) > 240 else "")
                out.append(f"  {snippet}")

    out.append("\n---")
    out.append(f"<sub>{t(tr, 'digest.tier_footer')}</sub>")
    return "\n".join(out)


def summarize_items(items: list[NewsItem], topics: list[str]) -> str:
    """HOOK para la síntesis con LLM (futuro).

    Aquí se llamaría a Claude para: agrupar historias relacionadas, redactar un
    resumen en el idioma del usuario y marcar el nivel de confianza. De momento
    delega en el render determinista.
    """
    raise NotImplementedError("Síntesis con LLM pendiente (siguiente fase).")
