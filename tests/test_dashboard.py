"""Tests para el dashboard de métricas.

Cubre:
  - _provider_of       (prefijo de proveedor, sin prefijo, cadena vacía)
  - _purpose_category  (summarize, translate_*, nacional_judge, fallback)
  - _purpose_label     (etiquetas finas con código de idioma)
  - DST de Santiago    (enero UTC-3, julio UTC-4, requiere tzdata)
  - _cost_breakdown    (agregación por categoría/proveedor, modelos sin precio)
  - _x_month_usage     (presupuesto del mes actual Santiago)
  - render_dashboard   (smoke test: HTML contiene secciones clave)
"""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from sibylla.dashboard import (
    _cost_breakdown,
    _provider_of,
    _purpose_category,
    _purpose_label,
    _x_month_usage,
    render_dashboard,
)
from sibylla.models import RunRecord

_SCL = ZoneInfo("America/Santiago")


# --- _provider_of -----------------------------------------------------------

PROVIDER_CASES = [
    ("anthropic:claude-opus-4-8", "anthropic", "prefijo anthropic"),
    ("deepseek:deepseek-v4-flash", "deepseek", "prefijo deepseek"),
    ("openai:gpt-4o", "openai", "prefijo openai"),
    ("gpt-4o", "desconocido", "sin prefijo"),
    ("", "desconocido", "cadena vacía"),
    ("proveedor:modelo:extra", "proveedor", "solo primer prefijo antes del primer ':'"),
]


@pytest.mark.parametrize("model,expected,_desc", PROVIDER_CASES)
def test_provider_of(model, expected, _desc):
    assert _provider_of(model) == expected


# --- _purpose_category ------------------------------------------------------

CATEGORY_CASES = [
    ("summarize", "Resumen", "resumen de IA"),
    ("translate_es", "Traducción", "traducción español"),
    ("translate_en", "Traducción", "traducción inglés"),
    ("translate_it", "Traducción", "traducción italiano"),
    ("translate_pt", "Traducción", "traducción portugués"),
    ("nacional_judge", "Juez Nacional", "juez de nacionalidad"),
    ("feature_futura", "Feature Futura", "fallback: propósito desconocido se prettifica"),
    ("otra_cosa", "Otra Cosa", "fallback con guion bajo"),
]


@pytest.mark.parametrize("purpose,expected,_desc", CATEGORY_CASES)
def test_purpose_category(purpose, expected, _desc):
    assert _purpose_category(purpose) == expected


# --- _purpose_label ---------------------------------------------------------

LABEL_CASES = [
    ("summarize", "Resumen", "resumen"),
    ("translate_es", "Traducción (es)", "traducción español"),
    ("translate_en", "Traducción (en)", "traducción inglés"),
    ("translate_it", "Traducción (it)", "traducción italiano"),
    ("translate_pt", "Traducción (pt)", "traducción portugués"),
    ("nacional_judge", "Juez Nacional", "juez nacional"),
    ("futuro_proposito", "Futuro Proposito", "fallback"),
]


@pytest.mark.parametrize("purpose,expected,_desc", LABEL_CASES)
def test_purpose_label(purpose, expected, _desc):
    assert _purpose_label(purpose) == expected


# --- DST Santiago -----------------------------------------------------------

def test_dst_santiago_enero_verano_utc_minus_3():
    """Enero → horario de verano (UTC-3)."""
    ts = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)
    scl = ts.astimezone(_SCL)
    assert scl.hour == 11


def test_dst_santiago_julio_invierno_utc_minus_4():
    """Julio → horario de invierno (UTC-4)."""
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    scl = ts.astimezone(_SCL)
    assert scl.hour == 10


# --- _cost_breakdown --------------------------------------------------------

def _make_run(
    run_id: str,
    llm_calls: list[dict] | None = None,
    x_reads: int = 0,
    x_cost: float = 0.0,
) -> RunRecord:
    """Helper: crea un RunRecord mínimo para tests de agregación."""
    tokens = sum(c.get("input", 0) + c.get("output", 0) for c in (llm_calls or []))
    return RunRecord(
        run_id=run_id,
        timestamp=datetime.now(timezone.utc),
        topics=["ai"],
        sources=["test"],
        items_raw=10,
        items_final=8,
        mode="ia",
        translate=True,
        llm_calls=llm_calls or [],
        tokens_total=tokens,
        duration_s=30.0,
        x_reads=x_reads,
        x_cost=x_cost,
    )


def test_cost_breakdown_agrega_por_categoria():
    """Varias llamadas con distintos propósitos → sumas correctas por categoría."""
    runs = [
        _make_run("r1", [
            {"purpose": "summarize", "model": "deepseek:deepseek-chat", "input": 1000, "output": 500},
            {"purpose": "translate_es", "model": "deepseek:deepseek-chat", "input": 200, "output": 100},
        ]),
        _make_run("r2", [
            {"purpose": "translate_en", "model": "deepseek:deepseek-chat", "input": 300, "output": 150},
        ]),
    ]
    bd = _cost_breakdown(runs)
    by_cat = {c["name"]: c["tokens"] for c in bd["by_category"]}
    # summarize: 1000+500=1500, translate: 200+100+300+150=750
    assert by_cat.get("Resumen") == 1500
    assert by_cat.get("Traducción") == 750


def test_cost_breakdown_agrega_por_proveedor():
    """Proveedores distintos → agregación separada."""
    runs = [
        _make_run("r1", [
            {"purpose": "summarize", "model": "deepseek:deepseek-chat", "input": 1000, "output": 0},
        ]),
        _make_run("r2", [
            {"purpose": "summarize", "model": "anthropic:claude-sonnet-4", "input": 500, "output": 0},
        ]),
    ]
    bd = _cost_breakdown(runs)
    prov_tokens = {p["name"]: p["tokens"] for p in bd["by_provider"]}
    assert prov_tokens.get("deepseek") == 1000
    assert prov_tokens.get("anthropic") == 500


def test_cost_breakdown_detecta_modelos_desconocidos():
    """Modelo fuera de _MODEL_PRICING → aparece en unknown."""
    runs = [
        _make_run("r1", [
            {"purpose": "summarize", "model": "deepseek:deepseek-chat", "input": 100, "output": 50},
            {"purpose": "summarize", "model": "proveedor:modelo-fantasma", "input": 200, "output": 100},
        ]),
        _make_run("r2", [
            {"purpose": "translate_es", "model": "proveedor:modelo-fantasma", "input": 50, "output": 25},
        ]),
    ]
    bd = _cost_breakdown(runs)
    unknown_models = {u["model"]: u["calls"] for u in bd["unknown"]}
    assert "proveedor:modelo-fantasma" in unknown_models
    assert unknown_models["proveedor:modelo-fantasma"] == 2


def test_cost_breakdown_sin_datos():
    """Sin corridas → has_data=False, listas vacías."""
    bd = _cost_breakdown([])
    assert bd["has_data"] is False
    assert bd["by_category"] == []
    assert bd["by_provider"] == []
    assert bd["unknown"] == []
    assert bd["llm_cost"] == 0.0
    assert bd["x_cost"] == 0.0


def test_cost_breakdown_incluye_costo_x():
    """x_cost de las corridas se suma aparte del costo LLM."""
    runs = [
        _make_run("r1", llm_calls=[], x_reads=5, x_cost=0.025),
        _make_run("r2", llm_calls=[], x_reads=3, x_cost=0.015),
    ]
    bd = _cost_breakdown(runs)
    assert bd["x_cost"] == pytest.approx(0.040)
    assert bd["x_reads"] == 8


# --- _x_month_usage ---------------------------------------------------------

def test_x_month_usage_mes_actual():
    """Corridas del mes actual Santiago → se suman."""
    ahora = datetime.now(timezone.utc).astimezone(_SCL)
    ts_este_mes = datetime(ahora.year, ahora.month, 5, 12, 0, tzinfo=timezone.utc)
    runs = [
        _make_run("r1", x_reads=10, x_cost=0.05),
    ]
    # Override timestamp para caer en el mes actual Santiago
    runs[0].timestamp = ts_este_mes

    usage = _x_month_usage(runs, cap=300)
    assert usage["reads"] == 10
    assert usage["cap"] == 300
    assert usage["pct"] == pytest.approx(10 / 300 * 100, 0.1)


def test_x_month_usage_fuera_de_mes_no_suma():
    """Corrida de otro mes → no cuenta para el mes actual."""
    ahora = datetime.now(timezone.utc).astimezone(_SCL)
    # Mes anterior en Santiago
    if ahora.month == 1:
        ts_otro_mes = datetime(ahora.year - 1, 12, 10, 12, 0, tzinfo=timezone.utc)
    else:
        ts_otro_mes = datetime(ahora.year, ahora.month - 1, 10, 12, 0, tzinfo=timezone.utc)

    runs = [_make_run("r1", x_reads=50, x_cost=0.25)]
    runs[0].timestamp = ts_otro_mes

    usage = _x_month_usage(runs, cap=300)
    assert usage["reads"] == 0
    assert usage["pct"] == 0.0


def test_x_month_usage_cap_cero():
    """Cap=0 → pct=0 (sin división por cero)."""
    runs = [_make_run("r1", x_reads=10, x_cost=0.05)]
    usage = _x_month_usage(runs, cap=0)
    assert usage["pct"] == 0.0
    assert usage["cap"] == 0


# --- render_dashboard -------------------------------------------------------


def _runs_fixture_json() -> str:
    """Genera un runs.json de prueba con dos corridas."""
    ahora = datetime.now(timezone.utc)
    return json.dumps([
        {
            "run_id": "20260621-1000",
            "timestamp": ahora.isoformat(),
            "topics": ["ai"],
            "sources": ["test"],
            "items_raw": 10,
            "items_final": 8,
            "mode": "ia",
            "translate": True,
            "llm_calls": [
                {"purpose": "summarize", "model": "deepseek:deepseek-chat",
                 "input": 1000, "output": 500},
                {"purpose": "translate_es", "model": "deepseek:deepseek-chat",
                 "input": 200, "output": 100},
            ],
            "tokens_total": 1800,
            "duration_s": 25.0,
            "x_reads": 2,
            "x_cost": 0.01,
        },
        {
            "run_id": "20260620-0900",
            "timestamp": (ahora - timedelta(hours=25)).isoformat(),
            "topics": ["medicine"],
            "sources": ["test2"],
            "items_raw": 5,
            "items_final": 5,
            "mode": "ia",
            "translate": False,
            "llm_calls": [
                {"purpose": "summarize", "model": "anthropic:claude-sonnet-4",
                 "input": 800, "output": 400},
            ],
            "tokens_total": 1200,
            "duration_s": 18.0,
            "x_reads": 0,
            "x_cost": 0.0,
        },
    ])


def test_render_dashboard_produce_html():
    """El HTML generado contiene las secciones clave y no tiene restos Jinja."""
    with tempfile.TemporaryDirectory() as td:
        runs_file = Path(td) / "runs.json"
        runs_file.write_text(_runs_fixture_json(), encoding="utf-8")

        out = render_dashboard(out_dir=Path(td), runs_path=runs_file)
        html = out.read_text(encoding="utf-8")

    # Secciones esperadas
    assert "Costo LLM" in html
    assert "Costo total" in html
    assert "Desglose de costos" in html
    assert "Por característica" in html
    assert "Por proveedor" in html
    assert "Tendencia de costo por día" in html
    assert "Historial" in html
    assert "Resumen" in html
    assert "Traducción (es)" in html
    # Sin restos Jinja
    assert "{{" not in html
    assert "{%" not in html


def test_render_dashboard_sin_datos():
    """Sin runs → muestra mensaje de 'sin datos'."""
    with tempfile.TemporaryDirectory() as td:
        runs_file = Path(td) / "runs.json"
        runs_file.write_text("[]", encoding="utf-8")

        out = render_dashboard(out_dir=Path(td), runs_path=runs_file)
        html = out.read_text(encoding="utf-8")

    assert "Sin ejecuciones registradas" in html
    assert "Costo LLM" not in html  # no aparece sin datos
