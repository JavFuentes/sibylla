"""Genera dashboard.html con métricas del proyecto: historial de ejecuciones y consumo de tokens."""
from __future__ import annotations

import hashlib
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .metrics import estimate_cost, load_runs
from .models import RunRecord

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"

_DEDUP_WINDOW = timedelta(minutes=2)


def _format_dt(dt: datetime) -> str:
    """'21 jun 2026, 13:55 UTC'."""
    months = ["ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{dt.day} {months[dt.month - 1]} {dt:%Y}, {dt:%H:%M} UTC"


def _format_day(dt: datetime) -> str:
    """'21 jun 2026' sin hora."""
    months = ["ene", "feb", "mar", "abr", "may", "jun",
              "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{dt.day} {months[dt.month - 1]} {dt:%Y}"


def _model_short(model: str) -> str:
    """Quita el prefijo de proveedor (openai_compatible:model -> model)."""
    if ":" in model:
        return model.rsplit(":", 1)[1]
    return model


def _dedup_runs(runs: list[RunRecord]) -> list[RunRecord]:
    """Elimina duplicados con mismo run_id en ventana de 2 minutos (conserva el último)."""
    if len(runs) < 2:
        return list(runs)
    # Orden cronológico: del más antiguo al más reciente
    sorted_runs = sorted(runs, key=lambda r: r.timestamp)
    kept: list[RunRecord] = []
    i = 0
    while i < len(sorted_runs):
        current = sorted_runs[i]
        # Avanza mientras el siguiente tenga mismo run_id y esté dentro de la ventana
        j = i + 1
        while (j < len(sorted_runs)
               and sorted_runs[j].run_id == current.run_id
               and (sorted_runs[j].timestamp - current.timestamp) <= _DEDUP_WINDOW):
            j += 1
        # Conserva el último del grupo (el más reciente)
        kept.append(sorted_runs[j - 1])
        i = j
    return kept


def _run_cost(run: RunRecord) -> float:
    """Costo total estimado de una ejecución (LLM + X)."""
    total = 0.0
    for call in run.llm_calls:
        c = estimate_cost(call.get("model", ""), call.get("input", 0), call.get("output", 0))
        if c is not None:
            total += c
    total += run.x_cost
    return total


def _prep_calls(llm_calls: list[dict]) -> list[dict[str, Any]]:
    """Prepara las llamadas LLM para la plantilla, con modelo acortado."""
    # Orden canónico: summarize primero, luego traducciones por idioma
    purpose_order = {"summarize": 0}
    for i, lang in enumerate(["es", "en", "it", "pt"], 1):
        purpose_order[f"translate_{lang}"] = i

    def _sort_key(c: dict) -> int:
        return purpose_order.get(c.get("purpose", ""), 99)

    sorted_calls = sorted(llm_calls, key=_sort_key)
    result: list[dict[str, Any]] = []
    for c in sorted_calls:
        ccost = estimate_cost(c.get("model", ""), c.get("input", 0), c.get("output", 0))
        result.append({
            "purpose": c.get("purpose", ""),
            "model": c.get("model", ""),
            "model_short": _model_short(c.get("model", "")),
            "input": c.get("input", 0),
            "output": c.get("output", 0),
            "total": c.get("input", 0) + c.get("output", 0),
            "cost": ccost,
        })
    return result


def _prep_run(r: RunRecord) -> dict[str, Any]:
    """Convierte un RunRecord al dict que espera la plantilla."""
    cost = _run_cost(r)
    calls = _prep_calls(r.llm_calls)
    return {
        "run_id": r.run_id,
        "ts": _format_dt(r.timestamp),
        "ts_day": _format_day(r.timestamp),
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
        "has_x": r.x_reads > 0,
        "has_llm": len(calls) > 0,
    }


def _prep_runs(runs: list[RunRecord]) -> list[dict[str, Any]]:
    """Prepara las ejecuciones para la plantilla (más recientes primero, dedup)."""
    clean = _dedup_runs(runs)
    return [_prep_run(r) for r in reversed(clean)]


def _group_by_day(prep: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa las ejecuciones por día. Cada grupo es colapsable."""
    days: list[dict[str, Any]] = []
    current_label = ""
    current_runs: list[dict[str, Any]] = []
    for r in prep:
        if r["ts_day"] != current_label:
            if current_runs:
                days.append(_day_group(current_label, current_runs))
            current_label = r["ts_day"]
            current_runs = [r]
        else:
            current_runs.append(r)
    if current_runs:
        days.append(_day_group(current_label, current_runs))
    return days


def _day_group(label: str, runs: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "label": label,
        "runs": runs,
        "runs_n": len(runs),
        "cost_day": sum(r["cost"] for r in runs),
        "tokens_day": sum(r["tokens_total"] for r in runs),
    }


def _summary(runs: list[RunRecord], prep: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula los totales para las cards de resumen."""
    total_tokens = sum(r.tokens_total for r in runs)
    total_items = sum(r.items_final for r in runs)
    total_llm_cost = 0.0
    total_x_cost = 0.0
    total_x_reads = 0
    for r in runs:
        total_x_reads += r.x_reads
        total_x_cost += r.x_cost
        for c in r.llm_calls:
            est = estimate_cost(c.get("model", ""), c.get("input", 0), c.get("output", 0))
            if est is not None:
                total_llm_cost += est
    total_cost = total_llm_cost + total_x_cost
    max_tokens_run = max((r.tokens_total for r in runs), default=0)
    runs_with_llm = sum(1 for r in runs if r.llm_calls)
    return {
        "runs_n": len(runs),
        "tokens_total": total_tokens,
        "items_total": total_items,
        "cost_total": total_cost,
        "llm_cost": total_llm_cost,
        "x_cost": total_x_cost,
        "x_reads_total": total_x_reads,
        "max_tokens_run": max_tokens_run,
        "runs_with_llm": runs_with_llm,
        "avg_tokens": round(total_tokens / max(1, runs_with_llm)),
        "has_x": total_x_reads > 0,
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
    raw_runs = load_runs()
    runs = _dedup_runs(raw_runs)
    prep = _prep_runs(runs)
    days = _group_by_day(prep)
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
        days=days,
        summary=summary,
        max_token=max_token,
        dash_key_hash=dash_key_hash,
        djb2_fallback=_djb2(dash_key) if dash_key else 0,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out
