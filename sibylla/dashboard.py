"""Genera dashboard.html con métricas del proyecto: historial de ejecuciones y consumo de tokens."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .metrics import estimate_cost, load_runs
from .models import RunRecord

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"


def _format_dt(dt: datetime) -> str:
    """'21 jun 2026, 13:55 UTC'."""
    months = ["ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{dt.day} {months[dt.month - 1]} {dt:%Y}, {dt:%H:%M} UTC"


def _run_cost(run: RunRecord) -> float:
    """Costo total estimado de una ejecución (LLM + X)."""
    total = 0.0
    for call in run.llm_calls:
        c = estimate_cost(call.get("model", ""), call.get("input", 0), call.get("output", 0))
        if c is not None:
            total += c
    total += run.x_cost
    return total


def _prep_runs(runs: list[RunRecord]) -> list[dict[str, Any]]:
    """Prepara los datos de cada run para la plantilla."""
    items: list[dict[str, Any]] = []
    for r in reversed(runs):  # más recientes primero
        cost = _run_cost(r)
        calls: list[dict[str, Any]] = []
        for c in r.llm_calls:
            ccost = estimate_cost(c.get("model", ""), c.get("input", 0), c.get("output", 0))
            calls.append({
                "purpose": c.get("purpose", ""),
                "model": c.get("model", ""),
                "input": c.get("input", 0),
                "output": c.get("output", 0),
                "total": c.get("input", 0) + c.get("output", 0),
                "cost": ccost,
            })
        items.append({
            "run_id": r.run_id,
            "ts": _format_dt(r.timestamp),
            "topics": ", ".join(r.topics),
            "sources_n": len(r.sources),
            "items_raw": r.items_raw,
            "items_final": r.items_final,
            "mode": r.mode.upper(),
            "translate": r.translate,
            "tokens_total": r.tokens_total,
            "cost": cost,
            "duration_s": r.duration_s,
            "calls": calls,
            "calls_n": len(calls),
            "x_reads": r.x_reads,
            "x_cost": r.x_cost,
        })
    return items


def _summary(runs: list[RunRecord], prep: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula los totales para las cards de resumen."""
    total_tokens = sum(r.tokens_total for r in runs)
    total_items = sum(r.items_final for r in runs)
    total_cost = 0.0
    for p in prep:
        total_cost += p["cost"]
    max_tokens_run = max((r.tokens_total for r in runs), default=0)
    return {
        "runs_n": len(runs),
        "tokens_total": total_tokens,
        "items_total": total_items,
        "cost_total": total_cost,
        "max_tokens_run": max_tokens_run,
    }


def _djb2(s: str) -> int:
    """Hash djb2 no criptográfico (fallback si crypto.subtle no está disponible)."""
    h = 5381
    for ch in s:
        h = ((h << 5) + h + ord(ch)) & 0xFFFFFFFF
    return h


def render_dashboard(out_dir: Path | None = None) -> Path:
    """Genera web/dashboard.html y devuelve la ruta.

    Siempre genera aunque no haya ejecuciones registradas (muestra "sin datos").
    Si DASHBOARD_KEY está configurada en el entorno, el HTML incluye control de
    acceso: sin ?key=<valor> en la URL solo se ve "Acceso restringido".
    Sin DASHBOARD_KEY el contenido es público (modo desarrollo local).
    """
    out_dir = out_dir or SITE_DIR
    runs = load_runs()
    prep = _prep_runs(runs)
    summary = _summary(runs, prep)
    max_token = summary["max_tokens_run"] or 1

    ahora = _format_dt(datetime.now(timezone.utc))

    dash_key = (os.getenv("DASHBOARD_KEY") or "").strip()
    dash_key_hash = hashlib.sha256(dash_key.encode()).hexdigest() if dash_key else ""

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("dashboard.html.j2")
    html = tmpl.render(
        generado=ahora,
        runs=prep,
        summary=summary,
        max_token=max_token,
        dash_key_hash=dash_key_hash,
        djb2_fallback=_djb2(dash_key) if dash_key else 0,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out
