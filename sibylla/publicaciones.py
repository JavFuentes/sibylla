"""Publicaciones propias de Sibylla (sección SIBYLLA de la portada).

Sibylla puede publicar noticias propias (anuncios del proyecto, notas
editoriales, novedades del sitio) sin pasar por el pipeline de fuentes
externas: cada publicación es un archivo Markdown con front-matter YAML en
`publicaciones/` (versionado en git, así el build de CI la ve sin pasos extra).

Formato del archivo (plantilla comentada en `publicaciones/_plantilla.md`):

    ---
    titulo: Título de la noticia           # obligatorio
    fecha: 2026-07-03                      # obligatorio (YYYY-MM-DD, admite hora)
    resumen: Bajada visible en la tarjeta  # opcional (sin ella, recorte del cuerpo)
    imagen: mi-imagen.png                  # opcional (archivo en static/ o URL)
    url: https://...                       # opcional (enlace externo de la tarjeta)
    publicado: true                        # opcional (false = borrador)
    ---
    Cuerpo opcional: se muestra en el acordeón "Resumen" de la tarjeta.

Reglas:
- Los archivos cuyo nombre empieza con `_` se ignoran (plantilla, notas).
- Un archivo malformado solo registra un `log.warning`: NUNCA rompe el build
  (misma filosofía de fallo aislado que los fetchers).
- Una `fecha` futura pospone la publicación hasta el primer build posterior
  (publicación programada gratis: el cron ya corre 2×/día).
- Sin `url`, el `dedup_key` de la tarjeta deriva del título: NO cambiar el
  título de una publicación ya desplegada (es su identidad estable: ancla
  pública `#n-...` y, a futuro, la clave del contenido social).

Estas publicaciones no se traducen ni piden resúmenes al LLM: se escriben en
español y el cuerpo del archivo ES el texto del acordeón.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone

import yaml

from .config import ROOT
from .models import NewsItem

log = logging.getLogger("sibylla")

# Carpeta versionada con las publicaciones (un archivo .md por noticia).
PUB_DIR = ROOT / "publicaciones"

SIBYLLA_SOURCE_ID = "sibylla"
SIBYLLA_SOURCE_NAME = "Sibylla"
# Máximo de tarjetas de la sección SIBYLLA (mismo tope que las demás secciones).
SIBYLLA_MAX_TOTAL = 6

# front-matter YAML delimitado por '---' al inicio del archivo; el resto es cuerpo.
_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?(.*)\Z", re.DOTALL)


def _to_datetime(v) -> datetime | None:
    """Normaliza la `fecha` del front-matter a datetime UTC aware.

    YAML entrega `date` para `2026-07-03` y `datetime` para `2026-07-03 12:00`;
    también se acepta una cadena ISO. Cualquier otra cosa -> None."""
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.strip())
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _parse_publicacion(text: str, nombre: str = "?") -> NewsItem | None:
    """Parsea el texto de una publicación a NewsItem (None si no es publicable).

    Pura (sin disco ni red) para poder testearla con cadenas. Devuelve None
    ante front-matter ausente/ilegible, campos obligatorios faltantes o
    `publicado: false`; en los casos de error deja un warning con el nombre
    del archivo para poder corregirlo."""
    m = _FRONT_MATTER_RE.match(text or "")
    if not m:
        log.warning("Publicación %s: sin front-matter '---'; se omite.", nombre)
        return None
    try:
        meta = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError as exc:
        log.warning("Publicación %s: front-matter YAML ilegible (%s); se omite.", nombre, exc)
        return None
    if not isinstance(meta, dict):
        log.warning("Publicación %s: el front-matter no es un mapa YAML; se omite.", nombre)
        return None

    if meta.get("publicado") is False:  # borrador explícito (default: publicado)
        return None

    titulo = str(meta.get("titulo") or "").strip()
    fecha = _to_datetime(meta.get("fecha"))
    if not titulo or fecha is None:
        log.warning("Publicación %s: falta 'titulo' o 'fecha' válida; se omite.", nombre)
        return None

    cuerpo = m.group(2).strip()
    return NewsItem(
        title=titulo,
        url=str(meta.get("url") or "").strip(),  # safe_link_url filtra esquemas raros
        source_id=SIBYLLA_SOURCE_ID,
        source_name=SIBYLLA_SOURCE_NAME,
        tier=1,  # Sibylla es la fuente primaria de sus propias publicaciones
        topics=[SIBYLLA_SOURCE_ID],
        published=fecha,
        summary=str(meta.get("resumen") or "").strip(),
        image=(str(meta.get("imagen")).strip() or None) if meta.get("imagen") else None,
        extra={"kind": "pub", "network": SIBYLLA_SOURCE_ID, "body": cuerpo},
    )


def seleccionar_publicaciones(items: list[NewsItem],
                              now: datetime | None = None) -> list[NewsItem]:
    """Descarta fechas futuras, ordena por fecha descendente y corta al tope."""
    now = now or datetime.now(timezone.utc)
    vigentes = [it for it in items if it.published and it.published <= now]
    vigentes.sort(key=lambda it: it.published, reverse=True)
    return vigentes[:SIBYLLA_MAX_TOTAL]


def load_publicaciones(now: datetime | None = None) -> list[NewsItem]:
    """Carga las publicaciones de `publicaciones/` listas para renderizar.

    Sin carpeta o sin archivos publicables devuelve [] y la sección SIBYLLA
    no se renderiza. Cada archivo falla de forma aislada."""
    if not PUB_DIR.is_dir():
        return []
    items: list[NewsItem] = []
    for path in sorted(PUB_DIR.glob("*.md")):
        if path.name.startswith("_"):
            continue
        try:
            it = _parse_publicacion(path.read_text(encoding="utf-8"), path.name)
        except Exception as exc:  # noqa: BLE001 — fallo aislado, nunca rompe el build
            log.warning("Publicación %s ilegible (%s); se omite.", path.name, exc)
            continue
        if it is not None:
            items.append(it)
    return seleccionar_publicaciones(items, now)
