"""Punto de entrada del ingestor.

Uso:
    python -m sibylla.cli --topics ai,medicine --max-per-source 10
    python -m sibylla.cli --topics space --sources google_news_rss,arxiv_api
    python -m sibylla.cli --topics ai --summarize off   # solo lista, sin LLM
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime

from .config import OUTPUT_DIR
from .digest import render_digest
from .fetchers import TOPIC_CONFIG
from .llm import LLMError
from .pipeline import DEFAULT_FREE_SOURCES, run_pipeline
from .summarize import summarize_digest

log = logging.getLogger("sibylla")


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(message)s",
    )


def main(argv: list[str] | None = None) -> int:
    # Evita mojibake/UnicodeEncodeError con acentos y símbolos en la consola de Windows.
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    parser = argparse.ArgumentParser(prog="sibylla", description="Ingestor de noticias de Sibylla")
    parser.add_argument("--topics", default="ai,medicine",
                        help=f"temas separados por coma. Disponibles: {', '.join(TOPIC_CONFIG)}")
    parser.add_argument("--max-per-source", type=int, default=10,
                        help="máximo de ítems por fuente y tema (def. 10)")
    parser.add_argument("--sources", default=None,
                        help=f"ids de fuente separados por coma (def. {', '.join(DEFAULT_FREE_SOURCES)})")
    parser.add_argument("--with-x", action="store_true",
                        help="incluye X/Twitter (DE PAGO; tope mensual en sources.yaml). Off por defecto")
    parser.add_argument("--summarize", choices=["auto", "off"], default="auto",
                        help="auto: redacta con LLM si está configurado en .env; off: solo lista determinista")
    parser.add_argument("--out", default=None, help="ruta del archivo de salida (.md)")
    parser.add_argument("-q", "--quiet", action="store_true", help="menos logs")
    args = parser.parse_args(argv)

    _setup_logging(verbose=not args.quiet)

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    if args.with_x:
        base = sources if sources is not None else list(DEFAULT_FREE_SOURCES)
        if "x_twitter" not in base:
            base.append("x_twitter")
        sources = base

    items, meta = run_pipeline(topics, sources_filter=sources, limit=args.max_per_source)
    if not items:
        print("No se obtuvieron ítems. Revisa los temas/fuentes o la conexión.")
        return 1

    lang = meta.get("default_user_language", "es")
    markdown = None
    if args.summarize != "off":
        try:
            markdown = summarize_digest(items, topics, lang=lang)
            if markdown is None:
                log.info("Sin LLM configurado en .env. Uso el resumen determinista.")
        except LLMError as exc:
            log.warning("LLM no disponible (%s). Uso el resumen determinista.", exc)

    used_llm = markdown is not None
    if markdown is None:
        markdown = render_digest(items, topics, meta)

    OUTPUT_DIR.mkdir(exist_ok=True)
    out_path = args.out or (OUTPUT_DIR / f"digest-{datetime.now():%Y%m%d-%H%M}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)

    modo = "IA" if used_llm else "lista determinista"
    print(f"\n✅ {len(items)} ítems · {modo} -> {out_path}\n")
    print("--- vista previa ---")
    preview = markdown.splitlines()
    print("\n".join(preview[:30]))
    if len(preview) > 30:
        print(f"... (+{len(preview) - 30} líneas en el archivo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
