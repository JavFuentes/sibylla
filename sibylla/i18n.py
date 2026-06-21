"""Internacionalización simple (sin dependencias). Carga traducciones desde JSON.

Idiomas soportados: es (defecto), en, it, pt.
Las claves se acceden con notación de punto: t("cli.no_items", error="...").
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

# Mapa de código de idioma a nombre en su propia lengua (usado en prompts LLM).
LANG_NAMES: dict[str, str] = {"es": "español", "en": "English", "it": "italiano", "pt": "português"}


@lru_cache(maxsize=8)
def load_translations(lang: str) -> dict[str, Any]:
    """Carga el archivo JSON del idioma pedido. Si no existe, lanza FileNotFoundError."""
    path = LOCALES_DIR / f"{lang}.json"
    if not path.exists():
        raise FileNotFoundError(f"Idioma '{lang}' no encontrado: {path}")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def resolve_lang(cli_lang: str | None = None, config_meta: dict | None = None) -> str:
    """Determina el idioma final aplicando prioridad:
    1. Flag --lang del CLI
    2. Variable de entorno SIBYLLA_LANG
    3. Campo default_user_language en config/sources.yaml
    4. Fallback: 'es'
    """
    lang = cli_lang or os.getenv("SIBYLLA_LANG") or ""
    if not lang and config_meta:
        lang = config_meta.get("default_user_language", "")
    lang = lang.strip().lower() or "es"
    # Validar que el archivo de traducción exista; si no, caer a 'es'.
    if not (LOCALES_DIR / f"{lang}.json").exists():
        lang = "es"
    return lang


def t(tr: dict[str, Any], key: str, **kwargs: Any) -> str:
    """Accede a una traducción por ruta con puntos. Ej: t(tr, 'cli.no_items').

    Las claves extra se usan como variables de formato: t(tr, 'cli.result_line', count=5).
    Si la clave no existe, devuelve la ruta entre signos '??'.
    """
    node: Any = tr
    for part in key.split("."):
        if isinstance(node, dict) and part in node:
            node = node[part]
        else:
            return f"??{key}??"
    result = node if isinstance(node, str) else str(node)
    if kwargs:
        try:
            result = result.format(**kwargs)
        except (KeyError, ValueError):
            pass
    return result
