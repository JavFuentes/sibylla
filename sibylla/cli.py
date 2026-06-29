"""Punto de entrada del ingestor.

Uso:
    python -m sibylla.cli --topics ai,medicine --max-per-source 10
    python -m sibylla.cli --topics space --sources google_news_rss,arxiv_api
    python -m sibylla.cli --topics ai --summarize off   # solo lista, sin LLM
    python -m sibylla.cli --lang en --html               # web en inglés
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from .config import OUTPUT_DIR
from .digest import render_digest
from .fetchers import TOPIC_CONFIG, x_usage_reads
from .i18n import load_translations, resolve_lang, t
from .llm import LLMError
from .models import RunRecord
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

    t0 = time.perf_counter()

    parser = argparse.ArgumentParser(prog="sibylla", description="Ingestor de noticias de Sibylla")
    parser.add_argument("--topics", default="nacional,ai,medicine,astronomia",
                        help=f"temas separados por coma. Disponibles: {', '.join(TOPIC_CONFIG)}")
    parser.add_argument("--max-per-source", type=int, default=10,
                        help="máximo de ítems por fuente y tema (def. 10)")
    parser.add_argument("--sources", default=None,
                        help=f"ids de fuente separados por coma (def. {', '.join(DEFAULT_FREE_SOURCES)})")
    parser.add_argument("--with-x", action="store_true",
                        help="incluye X/Twitter (DE PAGO; tope mensual en sources.yaml). Off por defecto")
    parser.add_argument("--summarize", choices=["auto", "off"], default="auto",
                        help="auto: redacta con LLM si está configurado en .env; off: solo lista determinista")
    parser.add_argument("--translate", choices=["auto", "off"], default="auto",
                        help="auto: traduce las tarjetas de la web al idioma de cada página si hay LLM; off: deja el idioma original")
    parser.add_argument("--out", default=None, help="ruta del archivo de salida (.md)")
    parser.add_argument("--html", action="store_true",
                        help="genera también la web estática (web/index.html) desde los ítems")
    parser.add_argument("--html-out", default=None,
                        help="ruta de salida de la web (def. web/index.html)")
    parser.add_argument("--lang", default=None,
                        help="idioma del resumen y la web: es, en, it, pt (def. según config, SIBYLLA_LANG, o 'es')")
    parser.add_argument("--dashboard", action="store_true",
                        help="abre el dashboard de métricas en local: descarga runs.json del host y lo muestra (no toca el sitio público)")
    parser.add_argument("-q", "--quiet", action="store_true", help="menos logs")
    args = parser.parse_args(argv)

    _setup_logging(verbose=not args.quiet)

    # Visor local del dashboard: no corre el pipeline, solo baja y abre métricas.
    if args.dashboard:
        from .dashboard import open_local_dashboard
        return open_local_dashboard()

    topics = [t.strip() for t in args.topics.split(",") if t.strip()]
    sources = [s.strip() for s in args.sources.split(",")] if args.sources else None
    if args.with_x:
        base = sources if sources is not None else list(DEFAULT_FREE_SOURCES)
        if "x_twitter" not in base:
            base.append("x_twitter")
        sources = base

    resolved_sources = sources or list(DEFAULT_FREE_SOURCES)

    x_before = x_usage_reads() if "x_twitter" in resolved_sources else 0
    items, meta, raw_count = run_pipeline(topics, sources_filter=sources, limit=args.max_per_source)
    x_after = x_usage_reads() if "x_twitter" in resolved_sources else 0
    x_reads = x_after - x_before
    x_cost = round(x_reads * 0.005, 6)  # ~$0.005/post

    # Sección Nacional: embudo heurístico + juez LLM elige los 6 (con cuota
    # regional). Reordena `items` dejando los elegidos al frente; degrada a
    # heurística pura si no hay LLM. Las llamadas LLM se contabilizan abajo.
    from .nacional import select_nacional, is_nacional
    items, nacional_calls = select_nacional(items)

    lang = resolve_lang(args.lang, meta)
    tr = load_translations(lang)

    if not items:
        print(t(tr, "cli.no_items"))
        return 1

    # X (y demás redes sociales) van SOLO a "Voces de la red" en la web; nunca a
    # las tarjetas de tema. El digest temático también las excluye, por coherencia.
    # Lo nacional también se excluye del resumen: ese digest es de ciencia y
    # tecnología, y mezclar noticia nacional chilena lo volvería incoherente.
    # (La sección Nacional vive solo en la web, ya seleccionada por select_nacional.)
    from .web import _is_social, _is_astro
    items_topic = [it for it in items
                   if not _is_social(it) and not is_nacional(it) and not _is_astro(it)]
    # El digest es de ciencia/tecnología: excluye 'nacional' y 'astronomia'
    # (ambas tienen su propia sección curada en la web).
    topics_sci = [tp for tp in topics if tp not in ("nacional", "astronomia")]

    # --- resumen (IA o determinista) ---
    llm_calls: list[dict] = list(nacional_calls)
    markdown = None

    if args.summarize != "off":
        try:
            result = summarize_digest(items_topic, topics_sci, lang=lang)
            if result is not None:
                markdown, calls = result
                llm_calls.extend(calls)
            else:
                log.info("Sin LLM configurado en .env. Uso el resumen determinista.")
        except LLMError as exc:
            log.warning("LLM no disponible (%s). Uso el resumen determinista.", exc)

    used_llm = markdown is not None
    if markdown is None:
        markdown = render_digest(items_topic, topics_sci, meta, lang=lang)

    OUTPUT_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    out_path = args.out or (OUTPUT_DIR / f"digest-{now:%Y%m%d-%H%M}.md")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(markdown)

    modo = t(tr, "cli.mode_ai") if used_llm else t(tr, "cli.mode_deterministic")
    print(f"\n{t(tr, 'cli.result_line', count=len(items), mode=modo, path=out_path)}\n")
    print("--- vista previa ---")
    preview = markdown.splitlines()
    print("\n".join(preview[:30]))
    if len(preview) > 30:
        print(f"... (+{len(preview) - 30} líneas en el archivo)")

    do_translate = args.translate != "off"

    if args.html:
        from .web import build_all_sites
        paths = build_all_sites(items, topics, meta, translate=do_translate,
                                translate_tracker=llm_calls, include_x=args.with_x)
        print(f"\n🌐 Web estática generada:")
        for p in paths:
            print(f"  {p}")

    # --- dashboard de métricas ---
    duration = time.perf_counter() - t0
    tokens_total = sum(c.get("input", 0) + c.get("output", 0) for c in llm_calls)
    run_id = f"{now:%Y%m%d-%H%M}"
    record = RunRecord(
        run_id=run_id,
        timestamp=now,
        topics=topics,
        sources=resolved_sources,
        items_raw=raw_count,
        items_final=len(items),
        mode="ia" if used_llm else "determinista",
        translate=do_translate,
        llm_calls=llm_calls,
        tokens_total=tokens_total,
        duration_s=round(duration, 1),
        x_reads=x_reads,
        x_cost=x_cost,
    )
    from .metrics import record_run
    record_run(record)

    # El dashboard de métricas NO se genera aquí: es una herramienta de monitoreo
    # local que se ve con `python -m sibylla.cli --dashboard` (lee data/runs.json).
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
