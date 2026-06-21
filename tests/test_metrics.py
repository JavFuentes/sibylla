"""Tests para métricas de ejecución: matching de modelos y estimación de costos.

Cubre:
  - _match_model   (búsqueda por prefijo, con/sin provider:, longest match)
  - estimate_cost  (cálculo USD, modelo desconocido, tokens cero/negativos)
"""
import pytest

from sibylla.metrics import _match_model, estimate_cost


# --- _match_model -----------------------------------------------------------

MATCH_CASES = [
    # (modelo, esperado, _desc)
    ("deepseek-chat", (0.140, 0.280), "exact match"),
    ("deepseek-v4-pro", (0.435, 0.870), "exact match v4-pro"),
    ("deepseek-v4-flash", (0.140, 0.280), "exact match v4-flash"),
    ("claude-sonnet-4", (3.0, 15.0), "exact match claude"),
    ("gpt-4o", (2.500, 10.000), "exact match openai"),
    ("gpt-4o-mini", (0.150, 0.600), "exact match openai mini"),
    # con prefijo proveedor:
    ("openai:gpt-4o", (2.500, 10.000), "con prefijo openai:"),
    ("anthropic:claude-sonnet-4", (3.0, 15.0), "con prefijo anthropic:"),
    ("openrouter:gpt-4o-mini", (0.150, 0.600), "con prefijo openrouter:"),
    # longest prefix match (versión concreta gana a genérico):
    ("deepseek-v4-pro-20250528", (0.435, 0.870), "sufijo extra → match por prefijo más largo"),
    ("claude-sonnet-4-6", (3.0, 15.0), "sub-versión → match por prefijo"),
    # sin match
    ("modelo-inexistente", None, "modelo no registrado"),
    ("", None, "cadena vacía"),
    ("gpt", None, "prefijo demasiado corto"),
    # prefijo proveedor pero modelo no existe
    ("openai:modelo-falso", None, "proveedor válido, modelo falso"),
]


@pytest.mark.parametrize("model,expected,_desc", MATCH_CASES)
def test_match_model(model, expected, _desc):
    assert _match_model(model) == expected


# --- estimate_cost ----------------------------------------------------------

def test_estimate_cost_known_model():
    """Cálculo normal para modelo con precio conocido."""
    cost = estimate_cost("deepseek-chat", 500_000, 100_000)
    expected = (500_000 / 1_000_000) * 0.140 + (100_000 / 1_000_000) * 0.280
    assert cost == pytest.approx(expected)


def test_estimate_cost_unknown_model():
    """Modelo sin entrada en la tabla → None."""
    assert estimate_cost("modelo-desconocido", 1000, 500) is None


def test_estimate_cost_zero_tokens():
    """0 tokens de entrada y salida → 0.0 USD."""
    assert estimate_cost("gpt-4o", 0, 0) == 0.0


def test_estimate_cost_negative_tokens_returns_zero():
    """Tokens negativos → early return 0.0 (input <= 0 and output <= 0)."""
    assert estimate_cost("gpt-4o", -5, -10) == 0.0


def test_estimate_cost_only_input_tokens():
    """Solo tokens de entrada, sin salida."""
    cost = estimate_cost("gpt-4o", 1_000_000, 0)
    assert cost == pytest.approx(2.500)


def test_estimate_cost_only_output_tokens():
    """Solo tokens de salida, sin entrada."""
    cost = estimate_cost("gpt-4o", 0, 1_000_000)
    assert cost == pytest.approx(10.000)


def test_estimate_cost_with_provider_prefix():
    """Prefijo proveedor:modelo se resuelve correctamente."""
    cost = estimate_cost("openai:gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost == pytest.approx(0.150 + 0.600)


def test_estimate_cost_input_zero_output_positive():
    """input=0 pero output>0 → pasa el guard (not (≤0 and ≤0)) y suma."""
    cost = estimate_cost("deepseek-chat", 0, 1_000_000)
    assert cost == pytest.approx(0.280)
