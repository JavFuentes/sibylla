"""Registro de métricas por ejecución: tokens, costos, historial para el dashboard."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from .config import ROOT
from .models import RunRecord

log = logging.getLogger("sibylla")

RUNS_PATH = ROOT / "data" / "runs.json"

# Precios en USD por 1M tokens (input, output). Investigados junio 2026.
_MODEL_PRICING: dict[str, tuple[float, float]] = {
    # DeepSeek
    "deepseek-v4-flash":  (0.140, 0.280),
    "deepseek-v4-pro":    (0.435, 0.870),
    "deepseek-chat":      (0.140, 0.280),   # legacy → v4-flash non-thinking
    "deepseek-reasoner":  (0.140, 0.280),   # legacy → v4-flash thinking

    # Anthropic Claude
    "claude-fable-5":     (10.000, 50.000),
    "claude-opus-4-8":    (5.000, 25.000),
    "claude-opus-4-7":    (5.000, 25.000),
    "claude-opus-4-6":    (5.000, 25.000),
    "claude-opus-4-5":    (5.000, 25.000),
    "claude-opus-4":      (15.000, 75.000),
    "claude-sonnet-4-6":  (3.000, 15.000),
    "claude-sonnet-4-5":  (3.000, 15.000),
    "claude-sonnet-4":    (3.000, 15.000),
    "claude-haiku-4-5":   (1.000, 5.000),

    # OpenAI
    "gpt-5.5":            (5.000, 30.000),
    "gpt-5.4":            (2.500, 15.000),
    "gpt-5.4-mini":       (0.750, 4.500),
    "gpt-5.3":            (1.250, 10.000),
    "gpt-4.1":            (2.000, 8.000),
    "gpt-4.1-mini":       (0.400, 1.600),
    "gpt-4o":             (2.500, 10.000),
    "gpt-4o-mini":        (0.150, 0.600),
}


def _match_model(model_lower: str) -> tuple[float, float] | None:
    """Busca el modelo en la tabla de precios. Acepta prefijo proveedor:modelo."""
    candidates = [model_lower]
    if ":" in model_lower:
        candidates.append(model_lower.rsplit(":", 1)[1])
    best_key = ""
    best_price = None
    for cand in candidates:
        for key, prices in _MODEL_PRICING.items():
            if cand.startswith(key) and len(key) > len(best_key):
                best_key = key
                best_price = prices
    return best_price


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estima el costo en USD de una llamada. None si el modelo no está en la tabla."""
    if input_tokens <= 0 and output_tokens <= 0:
        return 0.0
    model_lower = model.lower().replace(" ", "-")
    prices = _match_model(model_lower)
    if prices is None:
        return None
    price_in, price_out = prices
    return (input_tokens / 1_000_000) * price_in + (output_tokens / 1_000_000) * price_out


def load_runs(path=RUNS_PATH) -> list[RunRecord]:
    """Carga el historial de ejecuciones. [] si no hay archivo o está corrupto."""
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, list):
            return []
        runs: list[RunRecord] = []
        for d in data:
            if not isinstance(d, dict):
                continue
            ts = d.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            elif not isinstance(ts, datetime):
                ts = datetime.now(timezone.utc)
            runs.append(RunRecord(
                run_id=d.get("run_id", ""),
                timestamp=ts,
                topics=d.get("topics", []),
                sources=d.get("sources", []),
                items_raw=d.get("items_raw", 0),
                items_final=d.get("items_final", 0),
                mode=d.get("mode", "determinista"),
                translate=d.get("translate", False),
                llm_calls=d.get("llm_calls", []),
                tokens_total=d.get("tokens_total", 0),
                duration_s=d.get("duration_s", 0.0),
                x_reads=d.get("x_reads", 0),
                x_cost=d.get("x_cost", 0.0),
            ))
        return runs
    except (OSError, json.JSONDecodeError, (ValueError, TypeError)):
        return []


def record_run(record: RunRecord, path=RUNS_PATH) -> None:
    """Añade un registro de ejecución al historial persistente."""
    runs = load_runs(path)
    runs.append(record)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump([_as_dict(r) for r in runs], fh, ensure_ascii=False, indent=0, default=str)


def _as_dict(r: RunRecord) -> dict:
    return {
        "run_id": r.run_id,
        "timestamp": r.timestamp.isoformat(),
        "topics": r.topics,
        "sources": r.sources,
        "items_raw": r.items_raw,
        "items_final": r.items_final,
        "mode": r.mode,
        "translate": r.translate,
        "llm_calls": r.llm_calls,
        "tokens_total": r.tokens_total,
        "duration_s": r.duration_s,
        "x_reads": r.x_reads,
        "x_cost": r.x_cost,
    }
