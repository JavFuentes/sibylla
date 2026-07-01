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


def load_social_config(path: Path = CONFIG_PATH) -> dict:
    """Carga el bloque `social:` de sources.yaml (lentes, cuentas propias...)."""
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data.get("social", {}) or {}


def get_nasa_api_key() -> str:
    """Clave de la API de NASA (APOD). Lee NASA_API_KEY del .env; 'DEMO_KEY' si no
    se define (limite de tasa muy bajo, solo sirve para pruebas)."""
    return os.getenv("NASA_API_KEY", "DEMO_KEY").strip() or "DEMO_KEY"


def get_youtube_api_key() -> str:
    """Clave de la YouTube Data API v3 (sección Divulgación). Lee YOUTUBE_API_KEY
    del .env; vacío si no se define. Con clave, fetch_youtube usa la API oficial
    (robusta); sin clave, cae al feed RSS + caché (que YouTube throttlea desde IPs
    de datacenter con 404/500 engañosos)."""
    return os.getenv("YOUTUBE_API_KEY", "").strip()


def get_google_verification() -> str:
    """Token de verificación de Google Search Console (método 'etiqueta HTML').

    Se hornea como <meta name="google-site-verification"> en cada página, así
    sobrevive a la regeneración del sitio. Es solo el valor del atributo content
    (NO la etiqueta completa). Vacío si no se configura → no se emite la meta.
    """
    return os.getenv("SIBYLLA_GOOGLE_VERIFICATION", "").strip()
