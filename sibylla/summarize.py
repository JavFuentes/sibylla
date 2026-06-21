"""Síntesis del resumen con un LLM de proveedor configurable.

Devuelve Markdown ya redactado, o None si no hay LLM configurado en el entorno
(en ese caso el CLI usa el render determinista de digest.py).

Los prompts del LLM se cargan desde el archivo de traducción del idioma activo.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .i18n import load_translations, t
from .llm import get_provider
from .models import NewsItem

log = logging.getLogger("sibylla")


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

    tr = load_translations(lang)
    lang_name = t(tr, "summarize.lang_name")

    system = t(tr, "summarize.system_prompt", lang=lang_name)
    user = t(tr, "summarize.user_prompt",
             topics=", ".join(topics),
             date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
             lang=lang_name,
             items_json=json.dumps(_payload(items, max_items), ensure_ascii=False),
             )

    log.info("Resumiendo con %s (%s)…", provider.name, provider.model)
    text = provider.complete(system, user, max_tokens=max_tokens)

    header = t(tr, "summarize.header",
               topics=", ".join(topics),
               date=f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}",
               provider=provider.name,
               model=provider.model,
               count=len(items),
               )
    return header + text.strip() + "\n"
