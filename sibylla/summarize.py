"""Síntesis del resumen con un LLM de proveedor configurable.

Devuelve Markdown ya redactado, o None si no hay LLM configurado en el entorno
(en ese caso el CLI usa el render determinista de digest.py).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .llm import get_provider
from .models import NewsItem

log = logging.getLogger("sibylla")

SYSTEM_TMPL = (
    "Eres Sibylla, un asistente que redacta resúmenes de noticias de ciencia y "
    "tecnología. Escribe SIEMPRE en {lang}. Reglas estrictas:\n"
    "- Usa ÚNICAMENTE los ítems que te paso; no inventes hechos, cifras ni enlaces.\n"
    "- Cada historia enlaza a su fuente con enlaces Markdown reales tomados del campo 'url'.\n"
    "- Agrupa en una sola historia los ítems que tratan de la misma noticia.\n"
    "- Marca la confianza según el tier: T1 = primaria/peer-review (alta), "
    "T2 = periodismo (media), T3 = agregador/discusión (señal, sin confirmar).\n"
    "- Si una historia solo aparece en T3, dilo ('en discusión / sin confirmar').\n"
    "- Prioriza lo importante y reciente. Sé conciso y fácil de hojear.\n"
    "Devuelve SOLO Markdown, sin preámbulos."
)

USER_TMPL = (
    "Tema(s): {topics}\nFecha: {date}\n\n"
    "Redacta el resumen. Para cada tema incluye 3-6 historias clave. Por historia: "
    "título corto en negrita, 2-3 frases de síntesis en {lang}, los enlaces a las "
    "fuentes y una etiqueta de confianza.\n\n"
    "Ítems disponibles (JSON):\n{items_json}"
)


def _payload(items: list[NewsItem], max_items: int) -> list[dict]:
    rows = []
    for it in items[:max_items]:
        rows.append({
            "title": it.title,
            "url": it.url,
            "source": it.source_name,
            "tier": it.tier,
            "topic": it.topics[0] if it.topics else "",
            "date": it.published.strftime("%Y-%m-%d") if it.published else "",
            "snippet": it.summary[:300],
            "hn_points": it.extra.get("points"),
        })
    return rows


def summarize_digest(items: list[NewsItem], topics: list[str], lang: str = "es",
                     max_items: int = 24, max_tokens: int = 2500) -> str | None:
    """Redacta el resumen con el LLM configurado. None si no hay LLM."""
    provider = get_provider()
    if provider is None:
        return None

    system = SYSTEM_TMPL.format(lang=lang)
    user = USER_TMPL.format(
        topics=", ".join(topics),
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        lang=lang,
        items_json=json.dumps(_payload(items, max_items), ensure_ascii=False),
    )
    log.info("Resumiendo con %s (%s)…", provider.name, provider.model)
    text = provider.complete(system, user, max_tokens=max_tokens)

    header = (
        f"# Sibylla — Resumen ({', '.join(topics)})\n"
        f"_Generado {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC} · "
        f"redactado por {provider.name}:{provider.model} · {len(items)} ítems analizados_\n\n"
    )
    return header + text.strip() + "\n"
