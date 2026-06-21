"""Carga del registro de fuentes (config/sources.yaml) y del entorno (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "sources.yaml"
ENV_PATH = ROOT / ".env"
OUTPUT_DIR = ROOT / "output"


@dataclass
class Source:
    id: str
    name: str
    tier: int
    type: str
    topics: list[str] = field(default_factory=list)
    lang: str = ""
    access: str = ""
    cost: str = ""
    url: Optional[str] = None
    endpoint: Optional[str] = None
    url_template: Optional[str] = None
    notes: str = ""
    raw: dict = field(default_factory=dict)


def load_env() -> None:
    """Carga las claves del .env (si existe) en el entorno del proceso."""
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)


def load_registry(path: Path = CONFIG_PATH) -> tuple[dict, list[Source]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    meta = data.get("meta", {}) or {}
    sources: list[Source] = []
    for s in data.get("sources", []) or []:
        sources.append(Source(
            id=s["id"],
            name=s.get("name", ""),
            tier=int(s.get("tier", 3)),
            type=s.get("type", ""),
            topics=list(s.get("topics", []) or []),
            lang=s.get("lang", ""),
            access=s.get("access", ""),
            cost=str(s.get("cost", "")),
            url=s.get("url"),
            endpoint=s.get("endpoint"),
            url_template=s.get("url_template"),
            notes=s.get("notes", ""),
            raw=s,
        ))
    return meta, sources


def index_by_id(sources: list[Source]) -> dict[str, Source]:
    return {s.id: s for s in sources}


def get_site_url() -> str:
    """URL base del sitio público, sin barra final. Lee SIBYLLA_SITE_URL del .env; si no, usa el fallback."""
    return os.getenv("SIBYLLA_SITE_URL", "https://sibylla.cl").rstrip("/")
