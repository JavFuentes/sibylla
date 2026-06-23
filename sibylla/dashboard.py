"""Genera dashboard.html con métricas del proyecto: historial de ejecuciones y consumo de tokens."""
from __future__ import annotations

import os
import subprocess
import tempfile
import webbrowser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, load_env, load_registry, index_by_id
from .metrics import estimate_cost, load_runs
from .models import RunRecord

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"

_DEDUP_WINDOW = timedelta(minutes=2)
_SCL = ZoneInfo("America/Santiago")

_MESES = ["ene", "feb", "mar", "abr", "may", "jun",
          "jul", "ago", "sep", "oct", "nov", "dic"]


def _provider_of(model: str) -> str:
    """Prefijo antes de ':'. 'desconocido' si no hay prefijo."""
    if ":" in model:
        return model.split(":", 1)[0]
    return "desconocido"


def _purpose_category(purpose: str) -> str:
    """Categoría grupal del propósito (extensible: cualquier propósito nuevo aparece solo)."""
    if purpose == "summarize":
        return "Resumen"
    if purpose.startswith("translate_"):
        return "Traducción"
    if purpose == "nacional_judge":
        return "Juez Nacional"
    return purpose.replace("_", " ").title()


def _purpose_label(purpose: str) -> str:
    """Etiqueta fina para el detalle (ej. 'Traducción (es)')."""
    if purpose == "summarize":
        return "Resumen"
    if purpose.startswith("translate_"):
        lang = purpose[len("translate_"):]
        return f"Traducción ({lang})"
    if purpose == "nacional_judge":
        return "Juez Nacional"
    return purpose.replace("_", " ").title()


def _fmt_hora(dt: datetime) -> str:
    """HH:MM sin fecha."""
    return dt.strftime("%H:%M")


def _fmt_scl(dt: datetime) -> str:
    """Hora en Santiago (HH:MM)."""
    return _fmt_hora(dt.astimezone(_SCL))


def _fmt_utc(dt: datetime) -> str:
    """Hora en UTC (HH:MM)."""
    return _fmt_hora(dt.astimezone(timezone.utc))


def _fmt_day_scl(dt: datetime) -> str:
    """'21 jun 2026' en zona de Santiago."""
    scl = dt.astimezone(_SCL)
    return f"{scl.day} {_MESES[scl.month - 1]} {scl:%Y}"


def _fmt_dt_full(dt: datetime) -> str:
    """Tooltip completo: '21 jun 2026, 13:55 CLT / 17:55 UTC'."""
    scl = dt.astimezone(_SCL)
    utc = dt.astimezone(timezone.utc)
    return f"{_fmt_day_scl(dt)}, {_fmt_hora(scl)} CLT / {_fmt_hora(utc)} UTC"


def _format_dt(dt: datetime) -> str:
    """'21 jun 2026, 13:55 UTC'."""
    return f"{dt.day} {_MESES[dt.month - 1]} {dt:%Y}, {dt:%H:%M} UTC"


def _format_day(dt: datetime) -> str:
    """'21 jun 2026' sin hora."""
    return f"{dt.day} {_MESES[dt.month - 1]} {dt:%Y}"


def _model_short(model: str) -> str:
    """Quita el prefijo de proveedor (openai_compatible:model -> model)."""
    if ":" in model:
        return model.rsplit(":", 1)[1]
    return model


def _dedup_runs(runs: list[RunRecord]) -> list[RunRecord]:
    """Elimina duplicados con mismo run_id en ventana de 2 minutos (conserva el último)."""
    if len(runs) < 2:
        return list(runs)
    sorted_runs = sorted(runs, key=lambda r: r.timestamp)
    kept: list[RunRecord] = []
    i = 0
    while i < len(sorted_runs):
        current = sorted_runs[i]
        j = i + 1
        while (j < len(sorted_runs)
               and sorted_runs[j].run_id == current.run_id
               and (sorted_runs[j].timestamp - current.timestamp) <= _DEDUP_WINDOW):
            j += 1
        kept.append(sorted_runs[j - 1])
        i = j
    return kept


def _run_cost_llm(run: RunRecord) -> float:
    """Costo LLM solamente (excluye X)."""
    total = 0.0
    for call in run.llm_calls:
        c = estimate_cost(call.get("model", ""), call.get("input", 0), call.get("output", 0))
        if c is not None:
            total += c
    return total


def _prep_calls(llm_calls: list[dict]) -> list[dict[str, Any]]:
    """Prepara las llamadas LLM para la plantilla, con modelo acortado y etiquetas amigables."""
    purpose_order = {"summarize": 0}
    for i, lang in enumerate(["es", "en", "it", "pt"], 1):
        purpose_order[f"translate_{lang}"] = i

    def _sort_key(c: dict) -> int:
        return purpose_order.get(c.get("purpose", ""), 99)

    sorted_calls = sorted(llm_calls, key=_sort_key)
    result: list[dict[str, Any]] = []
    for c in sorted_calls:
        purpose = c.get("purpose", "")
        model = c.get("model", "")
        ccost = estimate_cost(model, c.get("input", 0), c.get("output", 0))
        result.append({
            "purpose": purpose,
            "purpose_label": _purpose_label(purpose),
            "model": model,
            "model_short": _model_short(model),
            "provider": _provider_of(model),
            "input": c.get("input", 0),
            "output": c.get("output", 0),
            "total": c.get("input", 0) + c.get("output", 0),
            "cost": ccost,
        })
    return result


def _prep_run(r: RunRecord) -> dict[str, Any]:
    """Convierte un RunRecord al dict que espera la plantilla."""
    cost_llm = _run_cost_llm(r)
    cost_x = r.x_cost
    cost_total = cost_llm + cost_x
    calls = _prep_calls(r.llm_calls)
    items = r.items_final if r.items_final > 0 else 0
    return {
        "run_id": r.run_id,
        "ts_scl": _fmt_scl(r.timestamp),
        "ts_utc": _fmt_utc(r.timestamp),
        "ts_full": _fmt_dt_full(r.timestamp),
        "ts_day": _fmt_day_scl(r.timestamp),
        "topics": ", ".join(r.topics),
        "sources_n": len(r.sources),
        "items_raw": r.items_raw,
        "items_final": r.items_final,
        "mode": r.mode.upper(),
        "translate": r.translate,
        "tokens_total": r.tokens_total,
        "cost": cost_total,
        "cost_llm": cost_llm,
        "cost_x": cost_x,
        "cost_per_item": cost_total / items if items else 0.0,
        "tokens_per_item": r.tokens_total / items if items else 0.0,
        "duration_s": r.duration_s,
        "calls": calls,
        "calls_n": len(calls),
        "x_reads": r.x_reads,
        "has_x": r.x_reads > 0,
        "has_llm": len(calls) > 0,
    }


def _prep_runs(runs: list[RunRecord]) -> list[dict[str, Any]]:
    """Prepara las ejecuciones para la plantilla (más recientes primero, dedup)."""
    clean = _dedup_runs(runs)
    return [_prep_run(r) for r in reversed(clean)]


def _group_by_day(prep: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Agrupa las ejecuciones por día (Santiago). Cada grupo es colapsable."""
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
        "cost_day_llm": sum(r["cost_llm"] for r in runs),
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
        "has_llm": total_llm_cost > 0,
    }


def _cost_breakdown(runs: list[RunRecord]) -> dict[str, Any]:
    """Agrega todo el historial por categoría, proveedor y modelo.

    Detecta modelos cuyo costo no se puede estimar (fuera de _MODEL_PRICING).
    """
    # Acumuladores
    by_cat: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0})
    by_prov: dict[str, dict[str, Any]] = defaultdict(lambda: {"tokens": 0, "cost": 0.0, "models": defaultdict(lambda: {"tokens": 0, "cost": 0.0})})
    unknown: dict[str, int] = {}  # model → count of calls
    total_llm_cost = 0.0
    total_x_cost = 0.0
    total_x_reads = 0

    for r in runs:
        total_x_cost += r.x_cost
        total_x_reads += r.x_reads
        for c in r.llm_calls:
            model = c.get("model", "")
            purpose = c.get("purpose", "")
            inp = c.get("input", 0)
            out = c.get("output", 0)
            tokens = inp + out
            cost = estimate_cost(model, inp, out)

            cat = _purpose_category(purpose)
            prov = _provider_of(model)

            by_cat[cat]["tokens"] += tokens
            by_prov[prov]["tokens"] += tokens
            by_prov[prov]["models"][model]["tokens"] += tokens

            if cost is not None:
                by_cat[cat]["cost"] += cost
                by_prov[prov]["cost"] += cost
                by_prov[prov]["models"][model]["cost"] += cost
                total_llm_cost += cost
            else:
                unknown[model] = unknown.get(model, 0) + 1

    # Convertir a listas ordenadas para la plantilla
    def _pct(v: float, total: float) -> float:
        return round(v / total * 100, 1) if total > 0 else 0.0

    # Por categoría
    by_category = sorted(
        [{"name": k, "tokens": v["tokens"], "cost": v["cost"], "pct": _pct(v["cost"], total_llm_cost)}
         for k, v in by_cat.items()],
        key=lambda x: x["cost"], reverse=True,
    )

    # Por proveedor (con modelos anidados)
    by_provider = []
    for prov_name, prov_data in sorted(by_prov.items(), key=lambda x: x[1]["cost"], reverse=True):
        models_list = sorted(
            [{"name": m, "tokens": d["tokens"], "cost": d["cost"],
              "pct": _pct(d["cost"], prov_data["cost"])}
             for m, d in prov_data["models"].items()],
            key=lambda x: x["cost"], reverse=True,
        )
        by_provider.append({
            "name": prov_name,
            "tokens": prov_data["tokens"],
            "cost": prov_data["cost"],
            "pct": _pct(prov_data["cost"], total_llm_cost),
            "models": models_list,
        })

    # Modelos desconocidos
    unknown_list = sorted(
        [{"model": m, "calls": n} for m, n in unknown.items()],
        key=lambda x: x["calls"], reverse=True,
    )

    return {
        "llm_cost": total_llm_cost,
        "x_cost": total_x_cost,
        "x_reads": total_x_reads,
        "by_category": by_category,
        "by_provider": by_provider,
        "unknown": unknown_list,
        "has_data": total_llm_cost > 0 or total_x_cost > 0,
    }


def _x_month_usage(runs: list[RunRecord], cap: int) -> dict[str, Any]:
    """Suma x_reads/x_cost de las corridas del mes actual en Santiago."""
    ahora_scl = datetime.now(timezone.utc).astimezone(_SCL)
    mes_actual = ahora_scl.month
    anio_actual = ahora_scl.year

    reads = 0
    cost = 0.0
    for r in runs:
        scl = r.timestamp.astimezone(_SCL)
        if scl.month == mes_actual and scl.year == anio_actual:
            reads += r.x_reads
            cost += r.x_cost

    return {
        "reads": reads,
        "cost": cost,
        "cap": cap,
        "pct": round(reads / cap * 100, 1) if cap > 0 else 0.0,
    }


def _load_x_cap() -> int:
    """Lee el presupuesto mensual de X desde config/sources.yaml. Fallback 300."""
    try:
        _, sources = load_registry()
        idx = index_by_id(sources)
        x_source = idx.get("x_twitter")
        if x_source and x_source.raw:
            return int(x_source.raw.get("monthly_read_budget", 300))
    except Exception:
        pass
    return 300


def render_dashboard(out_dir: Path | None = None, runs_path: Path | None = None) -> Path:
    """Genera dashboard.html y devuelve la ruta.

    Es una herramienta de monitoreo personal: el HTML se muestra sin reja de
    acceso. Siempre genera aunque no haya ejecuciones registradas (muestra
    "sin datos").

    `runs_path` permite renderizar desde un runs.json arbitrario; por defecto
    usa data/runs.json (el historial local).
    """
    out_dir = out_dir or SITE_DIR
    raw_runs = load_runs(runs_path) if runs_path is not None else load_runs()
    runs = _dedup_runs(raw_runs)
    prep = _prep_runs(runs)
    days = _group_by_day(prep)
    summary = _summary(runs, prep)
    max_token = summary["max_tokens_run"] or 1
    breakdown = _cost_breakdown(runs)
    x_cap = _load_x_cap()
    x_budget = _x_month_usage(runs, x_cap)

    ahora_utc = datetime.now(timezone.utc)
    ahora_scl = _fmt_dt_full(ahora_utc)
    generado_utc = _format_dt(ahora_utc)

    # Días del historial para la cabecera
    primer_dia = days[-1]["label"] if days else ""
    ultimo_dia = days[0]["label"] if days else ""

    # Máximo costo diario para las barras de tendencia
    max_cost_day = max((d["cost_day"] for d in days), default=0) or 1

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("dashboard.html.j2")
    html = tmpl.render(
        generado_utc=generado_utc,
        generado=ahora_scl,
        runs=prep,
        days=days,
        summary=summary,
        max_token=max_token,
        breakdown=breakdown,
        x_budget=x_budget,
        primer_dia=primer_dia,
        ultimo_dia=ultimo_dia,
        max_cost_day=max_cost_day,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "dashboard.html"
    out.write_text(html, encoding="utf-8")
    return out


# --- Visor local del dashboard (herramienta de monitoreo personal) ----------
# El dashboard NO se publica en el sitio: las métricas no le interesan al
# visitante. El historial de PRODUCCIÓN vive en el host (runs.json en
# DEPLOY_DATA_PATH); este comando lo descarga por SSH, lo renderiza en local
# (sin reja de acceso) y lo abre en el navegador.

def fetch_runs_from_host(dest: Path) -> None:
    """Descarga runs.json del host por scp usando las credenciales del .env.

    Lee DEPLOY_HOST, DEPLOY_USER, DEPLOY_PORT (def. 22), DEPLOY_DATA_PATH
    (def. '.sibylla') y, opcionalmente, DEPLOY_KEY_FILE (ruta a la clave
    privada; si se omite, usa tu config SSH por defecto / el agente).
    """
    host = os.getenv("DEPLOY_HOST")
    user = os.getenv("DEPLOY_USER")
    if not host or not user:
        raise SystemExit(
            "Faltan DEPLOY_HOST y/o DEPLOY_USER en .env. Añádelos (las mismas "
            "credenciales del deploy) para ver el historial de producción."
        )
    port = os.getenv("DEPLOY_PORT") or "22"
    remote_data = os.getenv("DEPLOY_DATA_PATH") or ".sibylla"
    key_file = os.getenv("DEPLOY_KEY_FILE")

    cmd = ["scp", "-P", str(port),
           "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=15"]
    if key_file:
        cmd += ["-i", os.path.expanduser(key_file)]
    cmd += [f"{user}@{host}:{remote_data}/runs.json", str(dest)]
    subprocess.run(cmd, check=True)


def open_local_dashboard() -> int:
    """Descarga el historial de producción del host, lo renderiza y lo abre.

    Pensado para correr en tu máquina (`python -m sibylla.cli --dashboard`).
    Se renderiza SIN reja de acceso: es un instrumento de medición privado.
    """
    load_env()

    with tempfile.TemporaryDirectory() as td:
        runs_tmp = Path(td) / "runs.json"
        print("→ Descargando historial de producción (runs.json) del host…")
        try:
            fetch_runs_from_host(runs_tmp)
        except FileNotFoundError:
            raise SystemExit("No encuentro el comando 'scp'. Instala el cliente OpenSSH.")
        except subprocess.CalledProcessError:
            raise SystemExit(
                "No se pudo descargar runs.json del host. ¿Ya corrió el workflow al "
                "menos una vez y existe DEPLOY_DATA_PATH/runs.json en el servidor?"
            )
        out = render_dashboard(runs_path=runs_tmp)

    print(f"✓ Dashboard generado en {out}")
    webbrowser.open(out.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(open_local_dashboard())
