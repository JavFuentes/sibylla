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

import random as _random
from datetime import datetime, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, get_google_verification, get_site_url, load_social_config
from .i18n import load_translations, t
from .models import NewsItem
from .pipeline import _social_score
from .translate import load_cache, save_cache, translate_cards

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"  # salida: web/{index,es,en,it,pt}.html
STATIC_DIR = ROOT / "static"  # assets a publicar tal cual (favicons, manifest, iconos)

# Etiqueta del idioma en su propia lengua (mayúsculas, para el selector).
LANG_LABELS = {"es": "ESPAÑOL", "en": "ENGLISH", "it": "ITALIANO", "pt": "PORTUGUÊS"}
ALL_LANGS = ["es", "en", "it", "pt"]

# Mapa de código de idioma → locale de Open Graph (sin guion bajo, con guion).
OG_LOCALE: dict[str, str] = {"es": "es_CL", "en": "en_US", "it": "it_IT", "pt": "pt_BR"}

# Máximo de medios "También en" que se listan en una tarjeta; el resto se resume como "+N".
_RELATED_CAP = 3

# Fuentes cuyos ítems se muestran en la sección "Voces de la red" (no en los temas).
SOCIAL_SOURCE_IDS: set[str] = {"x_twitter", "mastodon", "bluesky"}

# Máximo de tarjetas sociales visibles.
SOCIAL_MAX_TOTAL = 6


def _is_social(item: NewsItem) -> bool:
    """Indica si un ítem proviene de una red social (X, Mastodon…)."""
    return item.source_id in SOCIAL_SOURCE_IDS


def _select_social(organic: list[NewsItem],
                   house_items: list[NewsItem],
                   social_cfg: dict,
                   seed_str: str) -> list[NewsItem]:
    """Produce exactamente SOCIAL_MAX_TOTAL tarjetas para 'Voces de la red'.

    1. top‑1 por red (mastodon, bluesky, x_twitter) → hasta 3 slots.
    2. house cards (mejores 2 de `fetch_house_posts`); si <2, rellena con pool orgánico.
    3. Rellena huecos de redes que no aportaron nada con pool orgánico restante.
    4. Baraja si `social.shuffle` (semilla por día → estable dentro del día).
    """
    TOTAL = SOCIAL_MAX_TOTAL
    NETWORKS = ["mastodon", "bluesky", "x_twitter"]

    # Agrupar orgánico por source_id y rankear por _social_score
    by_net: dict[str, list[NewsItem]] = {n: [] for n in NETWORKS}
    for it in organic:
        if it.source_id in by_net:
            by_net[it.source_id].append(it)
    for posts in by_net.values():
        posts.sort(key=_social_score, reverse=True)

    used: set[int] = set()
    selected: list[NewsItem] = []

    # --- Fase 1: top‑1 por red ---
    for net in NETWORKS:
        for it in by_net.get(net, []):
            if id(it) not in used:
                selected.append(it)
                used.add(id(it))
                break  # solo 1 por red

    # --- Fase 2: house cards (hasta 2) ---
    house_ranked = sorted(house_items, key=_social_score, reverse=True)
    for it in house_ranked:
        if sum(1 for s in selected if s.extra.get("house")) >= 2:
            break
        if id(it) not in used:
            selected.append(it)
            used.add(id(it))

    # Si tras las fases 1+2 no llegamos a TOTAL, rellenar con orgánico restante
    if len(selected) < TOTAL:
        remaining_org = sorted(
            [it for it in organic if id(it) not in used],
            key=_social_score, reverse=True,
        )
        for it in remaining_org:
            if len(selected) >= TOTAL:
                break
            if id(it) not in used:
                selected.append(it)
                used.add(id(it))

    # Truncar a TOTAL por si acaso
    selected = selected[:TOTAL]

    # --- Fase 4: barajar ---
    if social_cfg.get("shuffle", True):
        rng = _random.Random(seed_str)
        rng.shuffle(selected)

    return selected

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
             translations: dict | None = None,
             resumenes: dict | None = None) -> dict:
    """Aplana un NewsItem a las claves que consume la plantilla.

    Si `translations` trae una entrada para este ítem (por `dedup_key`), usa el
    título y snippet traducidos; si no, cae al texto original.
    `resumenes` lleva (opcional) el resumen en español generado por LLM; si la
    tarjeta no tiene snippet de la fuente, cae a un recorte de ese resumen.
    """
    roman, clase, color = _SEAL.get(it.tier, _SEAL[3])
    tr = (translations or {}).get(it.dedup_key)
    title = tr["title"] if tr else it.title
    resumen = (resumenes or {}).get(it.dedup_key)
    snippet = (tr.get("snippet", "") if tr else "") or _snippet(it.summary)
    if not snippet and resumen:
        snippet = _snippet(resumen)
    # Otros medios con la misma historia (capados a 3; el resto, "+N").
    rel = it.related or []
    network = it.extra.get("network", "")
    if network == "x_twitter":
        network = "x"  # el locale usa la clave net_x (no net_x_twitter)
    is_house = bool(it.extra.get("house"))
    return {
        "url": it.url,
        "title": title,
        "source_name": it.source_name,
        "date": _fecha(it.published, months, no_date),
        "seal_roman": roman,
        "seal_class": clase,
        "seal_color": color,
        "snippet": snippet,
        "image": it.image,
        "resumen": resumen,
        "has_resumen": bool(resumen),
        "related": [{"source_name": r["source_name"], "url": r["url"]} for r in rel[:_RELATED_CAP]],
        "related_extra": max(0, len(rel) - _RELATED_CAP),
        "network": network,
        "is_house": is_house,
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
                    max_por_tema: int,
                    social_items: list[NewsItem] | None = None) -> list[NewsItem]:
    """Los NewsItem que se renderizarán como tarjetas (≤ max_por_tema por tema).

    Misma regla de selección que `_agrupar`, pero devuelve los ítems en vez de
    las tarjetas: lo usa la traducción para tocar solo lo visible (estrategia B+A).

    Si se pasan `social_items`, también se incluyen (siempre se renderizan)."""
    salida: list[NewsItem] = []
    for t in _orden_temas(items, topics):
        salida.extend([it for it in items if _primario(it) == t][:max_por_tema])
    if social_items:
        salida.extend(social_items)
    return salida


def _agrupar(items: list[NewsItem], topics: list[str],
             max_por_tema: int, topic_labels: dict[str, str],
             months: list[str], no_date: str,
             translations: dict | None = None,
             resumenes: dict | None = None) -> list[dict]:
    """Agrupa los ítems por su tema principal, en el orden pedido en `topics`.

    Los temas que aparezcan en los ítems pero no en `topics` (p. ej. 'otros')
    se añaden al final, para no perder señales.
    """
    grupos: list[dict] = []
    for t in _orden_temas(items, topics):
        cartas = [_tarjeta(it, months, no_date, translations, resumenes)
                  for it in items if _primario(it) == t][:max_por_tema]
        if cartas:
            grupos.append({"id": t, "label": _label(t, topic_labels), "cards": cartas})
    return grupos


def _render_jsonld(site_url: str, description: str,
                   cards: list[dict], lang: str) -> str:
    """Genera un bloque JSON-LD con WebSite + ItemList (NewsArticle)."""
    import json as _json

    def esc(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")

    partes: list[str] = []

    # WebSite.
    partes.append(
        '{"@context":"https://schema.org","@type":"WebSite",'
        f'"name":"Sibylla","url":"{site_url}","description":"{esc(description)}",'
        f'"inLanguage":"{lang}"'
        '}'
    )

    # ItemList con NewsArticle incrustados.
    if cards:
        items_json: list[str] = []
        for i, c in enumerate(cards, start=1):
            item = (
                '{"@type":"ListItem","position":' + str(i) + ','
                '"item":{"@type":"NewsArticle",'
                f'"headline":"{esc(c["title"])}",'
                f'"url":"{esc(c["url"])}",'
                f'"datePublished":"{esc(c["date"])}",'
                '"sourceOrganization":{"@type":"Organization",'
                f'"name":"{esc(c["source_name"])}"'
                '},'
                f'"description":"{esc(c["snippet"])}"'
            )
            if c.get("image"):
                item += f',"image":"{esc(c["image"])}"'
            item += "}}"
            items_json.append(item)
        partes.append(
            '{"@context":"https://schema.org","@type":"ItemList",'
            f'"itemListElement":[{",".join(items_json)}]'
            '}'
        )

    return "\n".join(partes)


def build_context(items: list[NewsItem], topics: list[str], meta: dict,
                   lang: str = "es", max_por_tema: int = 6,
                   is_landing: bool = False,
                   translations: dict | None = None,
                   social_items: list[NewsItem] | None = None,
                   resumenes: dict | None = None) -> dict:
    """Construye el contexto que recibe la plantilla.

    `items` son los ítems normales (temáticos). `social_items` son los ítems
    de redes sociales que van en su propia sección al pie de la página."""
    tr = load_translations(lang)
    tw = tr["web"]
    months: list[str] = tw["months"]
    no_date: str = tw["no_date"]
    topic_labels: dict[str, str] = tw["topics"]

    ahora = datetime.now(timezone.utc)
    # Contar fuentes contando también las sociales.
    all_source_ids = {it.source_id for it in items}
    if social_items:
        all_source_ids |= {it.source_id for it in social_items}
    n_fuentes = len(all_source_ids)
    total_all = len(items) + len(social_items) if social_items else len(items)

    grupos = _agrupar(items, topics, max_por_tema, topic_labels, months, no_date,
                      translations, resumenes)
    observa = t(tr, "web.voice", count=total_all, sources=n_fuentes)
    ts = _instante(ahora, months)
    hero_ts = t(tr, "web.hero_timestamp", date=ts, count=total_all, sources=n_fuentes)

    # Tarjetas sociales.
    social_cards: list[dict] = []
    if social_items:
        social_cards = [_tarjeta(it, months, no_date, translations, resumenes) for it in social_items]

    # Datos para el selector de idioma.
    lang_label = LANG_LABELS.get(lang, lang.upper())
    lang_options = [(lc, LANG_LABELS[lc]) for lc in ALL_LANGS if lc != lang]
    # Mapa código → nombre de archivo (es → es.html, excepto el default que es index.html).
    # Para que el JS de auto-detección sepa a dónde redirigir.
    lang_files = {lc: f"{lc}.html" for lc in ALL_LANGS}

    # Variables SEO.
    site_url = get_site_url()
    page_file = "index.html" if is_landing else f"{lang}.html"
    meta_description = tw.get("meta_description", "")
    og_locale = OG_LOCALE.get(lang, f"{lang}_{lang.upper()}")
    google_verification = get_google_verification()

    # JSON-LD WebSite + ItemList con todas las tarjetas visibles.
    all_cards: list[dict] = []
    for g in grupos:
        for c in g["cards"]:
            all_cards.append(c)
    jsonld = _render_jsonld(site_url, meta_description, all_cards, lang)

    return {
        "lang": lang,
        "is_landing": is_landing,
        "lang_label": lang_label,
        "lang_options": lang_options,
        "all_langs": ALL_LANGS,
        "lang_files": lang_files,
        "generado": ts,
        "hero_timestamp": hero_ts,
        "total": total_all,
        "n_fuentes": n_fuentes,
        "grupos": grupos,
        "social_cards": social_cards,
        "observa": observa,
        "t": tw,
        "site_url": site_url,
        "page_file": page_file,
        "meta_description": meta_description,
        "og_locale": og_locale,
        "google_verification": google_verification,
        "jsonld": jsonld,
    }


def render_html(items: list[NewsItem], topics: list[str], meta: dict,
                lang: str = "es", max_por_tema: int = 6,
                is_landing: bool = False,
                translations: dict | None = None,
                social_items: list[NewsItem] | None = None,
                resumenes: dict | None = None) -> str:
    """Renderiza la portada a una cadena HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("index.html.j2")
    return tmpl.render(**build_context(items, topics, meta, lang, max_por_tema,
                                       is_landing, translations, social_items,
                                       resumenes))


def _assert_min_items(items: list[NewsItem], min_n: int = 5) -> None:
    """Rechaza la generación si hay sospechosamente pocos ítems (evita que
    datos de prueba sobrescriban la salida real)."""
    if len(items) < min_n:
        raise ValueError(
            f"Se requieren al menos {min_n} ítems para generar "
            f"(solo hay {len(items)}). ¿Datos de prueba?"
        )


def _build_translations(items: list[NewsItem], topics: list[str],
                        max_por_tema: int,
                        tracker: list[dict] | None = None,
                        social_items: list[NewsItem] | None = None) -> dict[str, dict]:
    """Traduce las tarjetas RENDERIZADAS a cada idioma (estrategia B+A).

    Devuelve {lang: {dedup_key: {title, snippet}}}. Usa y actualiza el cache en
    disco (`data/translations.json`). Sin LLM configurado, los mapas quedan
    vacíos y las tarjetas caen a su idioma original aguas abajo.

    Si se pasa un `tracker`, se apendea el uso de tokens de cada llamada LLM.
    `social_items` se incluyen siempre en el lote de traducción (se renderizan
    todos, máximo 2)."""
    rendered = _rendered_items(items, topics, max_por_tema, social_items)
    # Cards únicas por dedup_key (deduplica por si un ítem se contara dos veces).
    cards: list[dict] = []
    vistos: set[str] = set()
    for it in rendered:
        if it.dedup_key in vistos:
            continue
        vistos.add(it.dedup_key)
        cards.append({"id": it.dedup_key, "title": it.title, "snippet": _snippet(it.summary)})

    cache = load_cache()
    by_lang = {lang: translate_cards(cards, lang, cache, tracker=tracker) for lang in ALL_LANGS}
    save_cache(cache)
    return by_lang


def _copy_static_assets() -> list[Path]:
    """Copia los assets estáticos (favicons, iconos, manifest) de static/ a web/.

    Todo lo que viva en `static/` se publica tal cual en la raíz del sitio. Para
    añadir o quitar un asset, basta con tocar la carpeta `static/` (sin código).
    Si `static/` no existe, no hace nada (la web se genera igual)."""
    if not STATIC_DIR.is_dir():
        return []
    copiados: list[Path] = []
    for src in sorted(STATIC_DIR.iterdir()):
        if not src.is_file():
            continue
        dst = SITE_DIR / src.name
        dst.write_bytes(src.read_bytes())
        copiados.append(dst)
    return copiados


def _write_robots(site_url: str) -> Path:
    """Escribe web/robots.txt."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {site_url}/sitemap.xml\n"
    )
    out = SITE_DIR / "robots.txt"
    out.write_text(content, encoding="utf-8")
    return out


def _write_sitemap(site_url: str, lastmod: str) -> Path:
    """Escribe web/sitemap.xml con todas las páginas y anotaciones hreflang."""
    urls: list[str] = []
    # Páginas de idioma.
    for lang in ALL_LANGS:
        loc = f"{site_url}/{lang}.html"
        alts = "\n".join(
            f'    <xhtml:link rel="alternate" hreflang="{lc}" href="{site_url}/{lc}.html"/>'
            for lc in ALL_LANGS
        )
        default = f'    <xhtml:link rel="alternate" hreflang="x-default" href="{site_url}/index.html"/>'
        urls.append(
            f"  <url>\n"
            f"    <loc>{loc}</loc>\n"
            f"    <lastmod>{lastmod}</lastmod>\n"
            f"    <changefreq>hourly</changefreq>\n"
            f"    <priority>0.9</priority>\n"
            f"{alts}\n"
            f"{default}\n"
            f"  </url>"
        )
    # Página de aterrizaje (x-default).
    loc_index = f"{site_url}/index.html"
    alts_index = "\n".join(
        f'    <xhtml:link rel="alternate" hreflang="{lc}" href="{site_url}/{lc}.html"/>'
        for lc in ALL_LANGS
    )
    default_index = f'    <xhtml:link rel="alternate" hreflang="x-default" href="{loc_index}"/>'
    urls.append(
        f"  <url>\n"
        f"    <loc>{loc_index}</loc>\n"
        f"    <lastmod>{lastmod}</lastmod>\n"
        f"    <changefreq>hourly</changefreq>\n"
        f"    <priority>1.0</priority>\n"
        f"{alts_index}\n"
        f"{default_index}\n"
        f"  </url>"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"\n'
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
        + "\n".join(urls) +
        "\n</urlset>\n"
    )
    out = SITE_DIR / "sitemap.xml"
    out.write_text(xml, encoding="utf-8")
    return out


def build_all_sites(items: list[NewsItem], topics: list[str], meta: dict,
                     max_por_tema: int = 6, translate: bool = True,
                     translate_tracker: list[dict] | None = None) -> list[Path]:
    """Genera un HTML por idioma + index.html de aterrizaje con auto-detección.

    Estructura generada:
        web/index.html  → landing (español + JS que redirige según navegador)
        web/es.html     → español
        web/en.html     → inglés
        web/it.html     → italiano
        web/pt.html     → portugués

    Si `translate` es True y hay LLM configurado, las tarjetas (título + snippet)
    se traducen al idioma de cada página; si no, quedan en su idioma original.

    Si se pasa `translate_tracker`, se apendea el uso de tokens de cada llamada LLM.

    Los ítems de redes sociales (X, Mastodon, Bluesky…) se extraen de la lista principal
    y se muestran en su propia sección al pie de la página ("Voces de la red")."""
    _assert_min_items(items)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    # Separar ítems normales de los de redes sociales.
    normal_items = [it for it in items if not _is_social(it)]
    social_raw = [it for it in items if _is_social(it)]

    # Fetch house posts (cuentas propias de Sibylla) y seleccionar 6 tarjetas.
    from .fetchers import fetch_house_posts
    sc = load_social_config()
    house_accounts = sc.get("house_accounts", [])
    house_items = fetch_house_posts(house_accounts)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    social_top = _select_social(social_raw, house_items, sc, today)

    by_lang = {}
    if translate:
        by_lang = _build_translations(normal_items, topics, max_por_tema,
                                      tracker=translate_tracker,
                                      social_items=social_top)

    # Resúmenes en español de las tarjetas renderizadas (botón "Resumen" + acordeón).
    # Se generan una sola vez (agnósticos al idioma de la página: siempre en ES).
    from .resumen import build_resumenes
    rendered_for_resumen = _rendered_items(normal_items, topics, max_por_tema, social_top)
    resumenes = build_resumenes(rendered_for_resumen, tracker=translate_tracker)

    # Páginas por idioma (sin auto-detección).
    for lang in ALL_LANGS:
        html = render_html(normal_items, topics, meta, lang=lang, max_por_tema=max_por_tema,
                           is_landing=False, translations=by_lang.get(lang),
                           social_items=social_top, resumenes=resumenes)
        out = SITE_DIR / f"{lang}.html"
        out.write_text(html, encoding="utf-8")
        paths.append(out)

    # Página de aterrizaje (español + JS de auto-detección).
    html_landing = render_html(normal_items, topics, meta, lang="es", max_por_tema=max_por_tema,
                                is_landing=True, translations=by_lang.get("es"),
                                social_items=social_top, resumenes=resumenes)
    out_landing = SITE_DIR / "index.html"
    out_landing.write_text(html_landing, encoding="utf-8")
    paths.append(out_landing)

    # Assets estáticos (favicons, iconos, manifest) → se publican en la raíz.
    paths.extend(_copy_static_assets())

    # Archivos SEO auxiliares.
    ahora = datetime.now(timezone.utc)
    site_url = get_site_url()
    paths.append(_write_robots(site_url))
    paths.append(_write_sitemap(site_url, ahora.strftime("%Y-%m-%d")))

    return paths


