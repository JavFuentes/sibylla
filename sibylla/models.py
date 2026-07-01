"""Modelo de datos normalizado y utilidades de texto/URL."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

# --- utilidades de texto ----------------------------------------------------
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def clean_text(s: str) -> str:
    """Quita etiquetas HTML, desescapa entidades y colapsa espacios."""
    if not s:
        return ""
    s = _TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return _WS_RE.sub(" ", s).strip()


def normalize_title(s: str) -> str:
    """Título en minúsculas, sin puntuación: para deduplicar por similitud."""
    s = clean_text(s).lower()
    s = _PUNCT_RE.sub("", s)
    return _WS_RE.sub(" ", s).strip()


# Parámetros de tracking que ensucian las URLs y rompen la deduplicación.
_TRACKING = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "gclid", "fbclid", "mc_cid", "mc_eid", "ref", "ref_src",
    "cmpid", "oc", "smid", "spm",
}


def safe_link_url(url: str) -> str:
    """Descarta esquemas peligrosos en un enlace saliente: solo http/https pasan.

    Los títulos/URLs de posts federados (Mastodon: cualquier instancia del
    fediverso) o de un feed comprometido no son de confianza. Sin este filtro,
    una URL `javascript:...` llegaría intacta al `href` de la tarjeta (Jinja2
    autoescapea el contenido del atributo, pero no valida el esquema). Devuelve
    "" si la URL no es http(s); el llamador ya tolera URL vacía (degrada a
    dedupe por título, sin enlace roto visible: no hay `<a>` sin href).
    """
    if not url:
        return ""
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        return ""
    return url.strip() if scheme in ("http", "https") else ""


def canonicalize_url(url: str) -> str:
    """Normaliza una URL para comparar: https, sin www, sin tracking, sin '/' final."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    if not parts.netloc:
        return url.strip().lower()
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    query = urlencode([
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k.lower() not in _TRACKING
    ])
    path = parts.path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


# --- modelo normalizado -----------------------------------------------------
@dataclass
class NewsItem:
    """Una noticia/ítem normalizado, venga de RSS o de una API."""
    title: str
    url: str
    source_id: str
    source_name: str
    tier: int
    topics: list[str] = field(default_factory=list)
    published: Optional[datetime] = None
    summary: str = ""
    image: Optional[str] = None  # URL de imagen (miniatura de la noticia/post); None = sin imagen
    authors: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # datos propios de la fuente (puntos HN, journal, pmid...)
    related: list[dict] = field(default_factory=list)  # otros medios con la misma historia: [{"source_name","url","tier"}]

    def __post_init__(self) -> None:
        self.title = clean_text(self.title)
        self.summary = clean_text(self.summary)
        self.url = safe_link_url(self.url)
        if self.published and self.published.tzinfo is None:
            self.published = self.published.replace(tzinfo=timezone.utc)

    @property
    def canonical_url(self) -> str:
        return canonicalize_url(self.url)

    @property
    def dedup_key(self) -> str:
        cu = self.canonical_url
        return ("u:" + cu) if cu else ("t:" + normalize_title(self.title))

    @property
    def age_hours(self) -> float:
        if not self.published:
            return 1e9
        return (datetime.now(timezone.utc) - self.published).total_seconds() / 3600.0


# --- respuesta del LLM -------------------------------------------------------
@dataclass
class LLMResponse:
    """Respuesta normalizada de un proveedor LLM con uso de tokens."""
    text: str
    usage: dict | None = None  # {"input": int, "output": int, "total": int}


# --- registro de ejecuciones (dashboard) ------------------------------------
@dataclass
class RunRecord:
    """Métrica de una regeneración completa del pipeline."""
    run_id: str              # "20260621-0955"
    timestamp: datetime      # UTC
    topics: list[str]
    sources: list[str]
    items_raw: int
    items_final: int
    mode: str                # "ia" | "determinista"
    translate: bool
    llm_calls: list[dict]    # [{"purpose":"summarize"|"translate_{lang}", "model":"...", "input":N, "output":N}]
    tokens_total: int
    duration_s: float
    x_reads: int = 0         # número de posts leídos de X en esta ejecución
    x_cost: float = 0.0      # costo estimado en USD de las lecturas de X (≈$0.005/post)
