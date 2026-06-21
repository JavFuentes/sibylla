"""Genera la web estática de Sibylla a partir de los ítems del pipeline.

Produce `web/index.html` (aterrizaje con auto-detección de idioma) y una
página por idioma: `web/es.html`, `web/en.html`, `web/it.html`, `web/pt.html`.

La portada es DETERMINISTA — no requiere LLM. La voz de Sibylla (los
"apuntes") se compone con plantillas simples a partir de los conteos.

El diseño vive en `sibylla/templates/index.html.j2` (fuente de verdad). Este
módulo solo lo alimenta con datos: para cambiar la estética, edita la plantilla,
no los HTML generados (se sobrescriben en cada corrida).

Las etiquetas de tema, meses y demás cadenas visibles se cargan desde el
archivo de traducción del idioma activo (locales/{lang}.json).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT
from .i18n import load_translations, t
from .models import NewsItem
from .translate import load_cache, save_cache, translate_cards

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"  # salida: web/{index,es,en,it,pt}.html

# Etiqueta del idioma en su propia lengua (mayúsculas, para el selector).
LANG_LABELS = {"es": "ESPAÑOL", "en": "ENGLISH", "it": "ITALIANO", "pt": "PORTUGUÊS"}
ALL_LANGS = ["es", "en", "it", "pt"]

# Tier -> (numeral romano, clase CSS del sello, color del acento de la tarjeta).
_SEAL = {
    1: ("I", "t1", "#F2DD93"),
    2: ("II", "t2", "#C7CEDB"),
    3: ("III", "t3", "#86B5A6"),
}


def _fecha(dt: datetime | None, months: list[str], no_date: str) -> str:
    """'21 jun 2026' (o etiqueta 's/f' si no hay fecha)."""
    if not dt:
        return no_date
    return f"{dt.day} {months[dt.month - 1]} {dt.year}"


def _instante(dt: datetime, months: list[str]) -> str:
    """'21 jun 2026, 06:54 UTC'."""
    return f"{dt.day} {months[dt.month - 1]} {dt.year}, {dt:%H:%M} UTC"


def _snippet(texto: str, limite: int = 220) -> str:
    """Recorta a `limite` caracteres en frontera de palabra, con elipsis."""
    s = (texto or "").strip()
    if len(s) <= limite:
        return s
    corte = s[:limite].rsplit(" ", 1)[0].rstrip(" ,.;:—-")
    return corte + "…"


def _label(topic: str, topic_labels: dict[str, str]) -> str:
    return topic_labels.get(topic, topic.replace("_", " ").capitalize())


def _tarjeta(it: NewsItem, months: list[str], no_date: str,
             translations: dict | None = None) -> dict:
    """Aplana un NewsItem a las claves que consume la plantilla.

    Si `translations` trae una entrada para este ítem (por `dedup_key`), usa el
    título y snippet traducidos; si no, cae al texto original.
    """
    roman, clase, color = _SEAL.get(it.tier, _SEAL[3])
    tr = (translations or {}).get(it.dedup_key)
    title = tr["title"] if tr else it.title
    snippet = tr["snippet"] if tr else _snippet(it.summary)
    return {
        "url": it.url,
        "title": title,
        "source_name": it.source_name,
        "date": _fecha(it.published, months, no_date),
        "seal_roman": roman,
        "seal_class": clase,
        "seal_color": color,
        "snippet": snippet,
    }


def _primario(it: NewsItem) -> str:
    """Tema principal del ítem ('otros' si no tiene)."""
    return it.topics[0] if it.topics else "otros"


def _orden_temas(items: list[NewsItem], topics: list[str]) -> list[str]:
    """Orden de temas a mostrar: primero los pedidos, luego los demás que
    aparezcan en los ítems; sin repetir."""
    orden = list(topics) + [_primario(it) for it in items]
    vistos: set[str] = set()
    salida: list[str] = []
    for t in orden:
        if t not in vistos:
            vistos.add(t)
            salida.append(t)
    return salida


def _rendered_items(items: list[NewsItem], topics: list[str],
                    max_por_tema: int) -> list[NewsItem]:
    """Los NewsItem que se renderizarán como tarjetas (≤ max_por_tema por tema).

    Misma regla de selección que `_agrupar`, pero devuelve los ítems en vez de
    las tarjetas: lo usa la traducción para tocar solo lo visible (estrategia B+A).
    """
    salida: list[NewsItem] = []
    for t in _orden_temas(items, topics):
        salida.extend([it for it in items if _primario(it) == t][:max_por_tema])
    return salida


def _agrupar(items: list[NewsItem], topics: list[str],
             max_por_tema: int, topic_labels: dict[str, str],
             months: list[str], no_date: str,
             translations: dict | None = None) -> list[dict]:
    """Agrupa los ítems por su tema principal, en el orden pedido en `topics`.

    Los temas que aparezcan en los ítems pero no en `topics` (p. ej. 'otros')
    se añaden al final, para no perder señales.
    """
    grupos: list[dict] = []
    for t in _orden_temas(items, topics):
        cartas = [_tarjeta(it, months, no_date, translations)
                  for it in items if _primario(it) == t][:max_por_tema]
        if cartas:
            grupos.append({"id": t, "label": _label(t, topic_labels), "cards": cartas})
    return grupos


def build_context(items: list[NewsItem], topics: list[str], meta: dict,
                  lang: str = "es", max_por_tema: int = 6,
                  is_landing: bool = False,
                  translations: dict | None = None) -> dict:
    """Construye el contexto que recibe la plantilla."""
    tr = load_translations(lang)
    tw = tr["web"]
    months: list[str] = tw["months"]
    no_date: str = tw["no_date"]
    topic_labels: dict[str, str] = tw["topics"]

    ahora = datetime.now(timezone.utc)
    n_fuentes = len({it.source_id for it in items})
    grupos = _agrupar(items, topics, max_por_tema, topic_labels, months, no_date, translations)
    observa = t(tr, "web.voice", count=len(items), sources=n_fuentes)
    ts = _instante(ahora, months)
    hero_ts = t(tr, "web.hero_timestamp", date=ts, count=len(items), sources=n_fuentes)

    # Datos para el selector de idioma.
    lang_label = LANG_LABELS.get(lang, lang.upper())
    lang_options = [(lc, LANG_LABELS[lc]) for lc in ALL_LANGS if lc != lang]
    # Mapa código → nombre de archivo (es → es.html, excepto el default que es index.html).
    # Para que el JS de auto-detección sepa a dónde redirigir.
    lang_files = {lc: f"{lc}.html" for lc in ALL_LANGS}

    return {
        "lang": lang,
        "is_landing": is_landing,
        "lang_label": lang_label,
        "lang_options": lang_options,
        "all_langs": ALL_LANGS,
        "lang_files": lang_files,
        "generado": ts,
        "hero_timestamp": hero_ts,
        "total": len(items),
        "n_fuentes": n_fuentes,
        "grupos": grupos,
        "observa": observa,
        "t": tw,
    }


def render_html(items: list[NewsItem], topics: list[str], meta: dict,
                lang: str = "es", max_por_tema: int = 6,
                is_landing: bool = False,
                translations: dict | None = None) -> str:
    """Renderiza la portada a una cadena HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("index.html.j2")
    return tmpl.render(**build_context(items, topics, meta, lang, max_por_tema,
                                       is_landing, translations))


def _assert_min_items(items: list[NewsItem], min_n: int = 5) -> None:
    """Rechaza la generación si hay sospechosamente pocos ítems (evita que
    datos de prueba sobrescriban la salida real)."""
    if len(items) < min_n:
        raise ValueError(
            f"Se requieren al menos {min_n} ítems para generar "
            f"(solo hay {len(items)}). ¿Datos de prueba?"
        )


def _build_translations(items: list[NewsItem], topics: list[str],
                        max_por_tema: int) -> dict[str, dict]:
    """Traduce las tarjetas RENDERIZADAS a cada idioma (estrategia B+A).

    Devuelve {lang: {dedup_key: {title, snippet}}}. Usa y actualiza el cache en
    disco (`data/translations.json`). Sin LLM configurado, los mapas quedan
    vacíos y las tarjetas caen a su idioma original aguas abajo.
    """
    rendered = _rendered_items(items, topics, max_por_tema)
    # Cards únicas por dedup_key (deduplica por si un ítem se contara dos veces).
    cards: list[dict] = []
    vistos: set[str] = set()
    for it in rendered:
        if it.dedup_key in vistos:
            continue
        vistos.add(it.dedup_key)
        cards.append({"id": it.dedup_key, "title": it.title, "snippet": _snippet(it.summary)})

    cache = load_cache()
    by_lang = {lang: translate_cards(cards, lang, cache) for lang in ALL_LANGS}
    save_cache(cache)
    return by_lang


def build_all_sites(items: list[NewsItem], topics: list[str], meta: dict,
                    max_por_tema: int = 6, translate: bool = True) -> list[Path]:
    """Genera un HTML por idioma + index.html de aterrizaje con auto-detección.

    Estructura generada:
        web/index.html  → landing (español + JS que redirige según navegador)
        web/es.html     → español
        web/en.html     → inglés
        web/it.html     → italiano
        web/pt.html     → portugués

    Si `translate` es True y hay LLM configurado, las tarjetas (título + snippet)
    se traducen al idioma de cada página; si no, quedan en su idioma original.
    """
    _assert_min_items(items)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    by_lang = _build_translations(items, topics, max_por_tema) if translate else {}

    # Páginas por idioma (sin auto-detección).
    for lang in ALL_LANGS:
        html = render_html(items, topics, meta, lang=lang, max_por_tema=max_por_tema,
                           is_landing=False, translations=by_lang.get(lang))
        out = SITE_DIR / f"{lang}.html"
        out.write_text(html, encoding="utf-8")
        paths.append(out)

    # Página de aterrizaje (español + JS de auto-detección).
    html_landing = render_html(items, topics, meta, lang="es", max_por_tema=max_por_tema,
                               is_landing=True, translations=by_lang.get("es"))
    out_landing = SITE_DIR / "index.html"
    out_landing.write_text(html_landing, encoding="utf-8")
    paths.append(out_landing)

    return paths


