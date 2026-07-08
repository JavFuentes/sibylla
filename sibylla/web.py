"""Genera la web estática de Sibylla a partir de los ítems del pipeline.

Produce una única página `web/index.html` (sitio monolingüe en español,
enfocado en Chile). No hay selector de idioma, página de aterrizaje con
auto-detección ni versiones por idioma.

La portada es DETERMINISTA en su estructura — no requiere LLM. La voz de
Sibylla (los "apuntes") se compone con plantillas simples a partir de los
conteos. El LLM entra solo en dos pasos opcionales de build: la traducción del
título+snippet al español (`translate.py`) y el resumen por tarjeta
(`resumen.py`); ambos degradan con elegancia si no hay LLM.

El diseño vive en `sibylla/templates/index.html.j2` (fuente de verdad). Este
módulo solo lo alimenta con datos: para cambiar la estética, edita la plantilla,
no los HTML generados (se sobrescriben en cada corrida).

Las etiquetas de tema, meses y demás cadenas visibles se cargan desde el
archivo de traducción del español (locales/es.json).
"""
from __future__ import annotations

import hashlib
import json
import logging
import random as _random
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .apod import APOD_SOURCE_ID, build_apod_card, build_apod_i18n, fetch_apod
from .config import ROOT, get_google_verification, get_nasa_api_key, get_site_url, load_env, load_social_config
from .i18n import load_translations, t
from .models import NewsItem
from .pipeline import _score, _social_score
from .translate import load_cache, save_cache, translate_cards

log = logging.getLogger("sibylla")

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
SITE_DIR = ROOT / "web"  # salida: web/{index,es,en,it,pt}.html
STATIC_DIR = ROOT / "static"  # assets a publicar tal cual (favicons, manifest, iconos)

# Etiqueta del idioma en su propia lengua (mayúsculas, para el selector).
LANG_LABELS = {"es": "ESPAÑOL", "en": "ENGLISH", "it": "ITALIANO", "pt": "PORTUGUÊS"}
# El sitio es monolingüe (español, enfocado en Chile). Se mantiene como lista
# para no romper el molde del código, pero solo hay una página: index.html.
ALL_LANGS = ["es"]

# Mapa de código de idioma → locale de Open Graph (sin guion bajo, con guion).
OG_LOCALE: dict[str, str] = {"es": "es_CL", "en": "en_US", "it": "it_IT", "pt": "pt_BR"}

# Máximo de medios "También en" que se listan en una tarjeta; el resto se resume como "+N".
_RELATED_CAP = 3

# Archivo publico consumido por Stellar-View.
STELLAR_NEWS_FILE = "stellar-news.json"
STELLAR_NEWS_SCHEMA = "cl.sibylla.stellar_news.v1"
STELLAR_NEWS_LANGS = ("es", "en", "it")

# Historial de destacadas de Stellar-View: se persiste en el HOST (como
# x_recent.json), NO se publica a la web. Sirve para no repetir la misma
# noticia dia tras dia. Guarda una entrada {date, id, source_id} por corrida
# que produzca destacada; se conservan las ultimas STELLAR_HISTORY_MAX (~1 mes).
STELLAR_HISTORY_SCHEMA = "cl.sibylla.stellar_history.v1"
STELLAR_HISTORY_MAX = 30
# "Dias desde la ultima vez que fue destacada" para una noticia nunca destacada:
# sentinela grande para que domine sobre cualquiera ya mostrada.
_STELLAR_NEVER_FEATURED = 10**6

# Sidecar de traduccion es/it del APOD de HOY (ver sibylla/apod.py).
APOD_I18N_FILE = "apod-i18n.json"
# Archivo historico: una copia por fecha que nunca se sobreescribe, para que
# Stellar-View pueda mostrar traducciones de APODs anteriores (desde que este
# archivo empezo a persistirse; los dias previos caen al ingles de NASA).
APOD_I18N_ARCHIVE_DIR = "apod-i18n"

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
    2. 2 house cards por RECENCIA (último post/repost), 1 por red distinta;
       misma red solo si ninguna otra aportó. Ignora el engagement.
    3. Rellena huecos de redes que no aportaron nada con pool orgánico restante.
    4. Baraja si `social.shuffle` (semilla por día → estable dentro del día).
    """
    TOTAL = SOCIAL_MAX_TOTAL
    HOUSE_SLOTS = 2
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

    # --- Fase 2: 2 house cards, redes distintas, por recencia (sin engagement) ---
    # A diferencia del orgánico (que rankea por buzz), las tarjetas de cuentas
    # propias se eligen por la actividad MÁS RECIENTE (último post o repost),
    # ignorando likes/reposts. Se exige diversidad de red: 1 por red distinta;
    # misma red solo si ninguna otra aportó posts.
    def _house_recency(it: NewsItem):
        return (it.extra.get("feed_ts") or it.published
                or datetime.min.replace(tzinfo=timezone.utc))

    house_by_net: dict[str, list[NewsItem]] = {}
    for it in house_items:
        house_by_net.setdefault(it.extra.get("network", "?"), []).append(it)
    for posts in house_by_net.values():
        posts.sort(key=_house_recency, reverse=True)

    # Redes ordenadas por su post más nuevo -> 1 tarjeta por red distinta.
    nets_by_recency = sorted(
        house_by_net, key=lambda n: _house_recency(house_by_net[n][0]), reverse=True)
    house_pick = [house_by_net[n][0] for n in nets_by_recency[:HOUSE_SLOTS]]
    # Menos redes que cupos -> rellenar con lo siguiente más reciente (misma red).
    if len(house_pick) < HOUSE_SLOTS:
        taken = {id(x) for x in house_pick}
        rest = sorted(
            (it for ps in house_by_net.values() for it in ps if id(it) not in taken),
            key=_house_recency, reverse=True)
        house_pick += rest[:HOUSE_SLOTS - len(house_pick)]
    for it in house_pick:
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


# =============================================================================
# Sección Astronomía — selección curada con slots reservados
# =============================================================================

ASTRO_PRIORITY_IDS: set[str] = {"alma", "cata", "sochias"}
ASTRO_AGENCY_IDS: set[str] = {"nasa", "esa", "jaxa", "cnes", "asi", "uksa"}
ASTRO_SOURCE_IDS: set[str] = ASTRO_PRIORITY_IDS | ASTRO_AGENCY_IDS
ASTRO_MAX_TOTAL = 6
# Ventanas de frescura distintas por bloque: las fuentes chilenas (observatorios/
# instituciones) publican mucho menos seguido que las agencias, así que su slot
# reservado tolera contenido más viejo (30 días) antes de ceder. Las agencias
# compiten por pocos cupos y se quiere lo más fresco (7 días).
ASTRO_PRIORITY_FRESH_DAYS = 30
ASTRO_AGENCY_FRESH_DAYS = 7


def _is_astro(item: NewsItem) -> bool:
    return item.source_id in ASTRO_SOURCE_IDS


# =============================================================================
# Sección Divulgación — videos de YouTube, 1 por canal
# =============================================================================

DIVULGACION_MAX_TOTAL = 6
# Ventana amplia: algunos canales curados publican con frecuencia baja.
DIVULGACION_FRESH_DAYS = 365


def _is_divulgacion(item: NewsItem) -> bool:
    """True si el ítem pertenece a la sección especial Divulgación."""
    return "divulgacion" in item.topics and item.source_id.startswith("yt_")


def _select_divulgacion(items: list[NewsItem]) -> list[NewsItem]:
    """Selecciona hasta 6 videos: 1 por canal, más recientes primero."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DIVULGACION_FRESH_DAYS)

    def _recency(it: NewsItem):
        return it.published or datetime.min.replace(tzinfo=timezone.utc)

    best_by_channel: dict[str, NewsItem] = {}
    for it in items:
        if _recency(it) < cutoff:
            continue
        cur = best_by_channel.get(it.source_id)
        if cur is None or _recency(it) > _recency(cur):
            best_by_channel[it.source_id] = it

    picks = sorted(best_by_channel.values(), key=_recency, reverse=True)
    return picks[:DIVULGACION_MAX_TOTAL]


def _select_astronomia(items: list[NewsItem], seed_str: str) -> list[NewsItem]:
    """Selecciona 6 tarjetas para la sección Astronomía.

    Bloque chileno (3 cupos): ALMA / CATA / SOCHIAS. 1 reservada por fuente
    si tiene contenido de ≤30 días; si no, cede su cupo a las otras chilenas.

    Bloque agencias (3 cupos): la más reciente por agencia (≤7 días primero;
    respaldo con más viejas; máx. 1 por agencia salvo imposible).

    Relleno cruzado: si un bloque no llena 3, el otro toma los cupos sobrantes.

    Orden: tarjeta 1 = chilena más reciente, tarjeta 2 = agencia más reciente,
    posiciones 3–6 aleatorias (semilla por día).
    """
    from datetime import timedelta

    TOTAL = ASTRO_MAX_TOTAL
    BLOCK = TOTAL // 2  # 3 por bloque
    now = datetime.now(timezone.utc)
    pri_cutoff = now - timedelta(days=ASTRO_PRIORITY_FRESH_DAYS)  # chilenas: 30 días
    ag_cutoff = now - timedelta(days=ASTRO_AGENCY_FRESH_DAYS)     # agencias: 7 días

    # Agrupar por fuente y ordenar por recencia
    pri_by_src: dict[str, list[NewsItem]] = {}
    ag_by_src: dict[str, list[NewsItem]] = {}
    for it in items:
        if it.source_id in ASTRO_PRIORITY_IDS:
            pri_by_src.setdefault(it.source_id, []).append(it)
        elif it.source_id in ASTRO_AGENCY_IDS:
            ag_by_src.setdefault(it.source_id, []).append(it)
    def _recency(it: NewsItem):
        return it.published or datetime.min.replace(tzinfo=timezone.utc)
    for lst in list(pri_by_src.values()) + list(ag_by_src.values()):
        lst.sort(key=_recency, reverse=True)

    used: set[int] = set()

    # --- Bloque chileno: 1 reservada por fuente (≤30 días), cede si no ---
    chilenas: list[NewsItem] = []
    for sid in ("alma", "cata", "sochias"):
        cands = pri_by_src.get(sid, [])
        fresh = [it for it in cands if _recency(it) >= pri_cutoff]
        pick = fresh[0] if fresh else None
        if pick:
            chilenas.append(pick)
            used.add(id(pick))

    # Rellenar cupos chilenos con lo que sobre de las otras fuentes chilenas
    if len(chilenas) < BLOCK:
        pool = sorted(
            [it for lst in pri_by_src.values() for it in lst
             if id(it) not in used and _recency(it) >= pri_cutoff],
            key=_recency, reverse=True)
        for it in pool:
            if len(chilenas) >= BLOCK:
                break
            chilenas.append(it)
            used.add(id(it))

    # --- Bloque agencias: máx 1 por agencia, las más recientes ---
    agencias: list[NewsItem] = []
    # Primero las frescas (≤7 días)
    fresh_ag: list[tuple[NewsItem, str]] = []
    for sid, cands in ag_by_src.items():
        for it in cands:
            if _recency(it) >= ag_cutoff and id(it) not in used:
                fresh_ag.append((it, sid))
                break
    fresh_ag.sort(key=lambda x: _recency(x[0]), reverse=True)
    used_agencies: set[str] = set()
    for it, sid in fresh_ag:
        if len(agencias) >= BLOCK:
            break
        if sid not in used_agencies:
            agencias.append(it)
            used.add(id(it))
            used_agencies.add(sid)

    # Respaldo: si faltan, tomar lo más reciente (sin límite de edad)
    if len(agencias) < BLOCK:
        backup: list[tuple[NewsItem, str]] = []
        for sid, cands in ag_by_src.items():
            if sid in used_agencies:
                continue
            for it in cands:
                if id(it) not in used:
                    backup.append((it, sid))
                    break
        backup.sort(key=lambda x: _recency(x[0]), reverse=True)
        for it, sid in backup:
            if len(agencias) >= BLOCK:
                break
            agencias.append(it)
            used.add(id(it))
            used_agencies.add(sid)

    # Último recurso: repetir agencia si es imposible llenar de otro modo
    if len(agencias) < BLOCK:
        rest = sorted(
            [it for lst in ag_by_src.values() for it in lst if id(it) not in used],
            key=_recency, reverse=True)
        for it in rest:
            if len(agencias) >= BLOCK:
                break
            agencias.append(it)
            used.add(id(it))

    # --- Relleno cruzado: mantener siempre TOTAL tarjetas ---
    total_got = len(chilenas) + len(agencias)
    if total_got < TOTAL:
        deficit = TOTAL - total_got
        # Rellenar con agencias no usadas (máx 1 por agencia nueva)
        extra_ag = sorted(
            [it for lst in ag_by_src.values() for it in lst
             if id(it) not in used and it.source_id not in used_agencies],
            key=_recency, reverse=True)
        for it in extra_ag:
            if deficit <= 0:
                break
            agencias.append(it)
            used.add(id(it))
            used_agencies.add(it.source_id)
            deficit -= 1
        # Si aún faltan, rellenar con chilenas no usadas
        if deficit > 0:
            extra_cl = sorted(
                [it for lst in pri_by_src.values() for it in lst if id(it) not in used],
                key=_recency, reverse=True)
            for it in extra_cl:
                if deficit <= 0:
                    break
                chilenas.append(it)
                used.add(id(it))
                deficit -= 1
        # Último recurso: repetir agencia ya usada
        if deficit > 0:
            extra_any = sorted(
                [it for lst in ag_by_src.values() for it in lst if id(it) not in used],
                key=_recency, reverse=True)
            for it in extra_any:
                if deficit <= 0:
                    break
                agencias.append(it)
                used.add(id(it))
                deficit -= 1

    # --- Orden final ---
    # Tarjeta 1 = chilena más reciente, tarjeta 2 = agencia más reciente
    chilenas.sort(key=_recency, reverse=True)
    agencias.sort(key=_recency, reverse=True)

    slot1 = chilenas[0] if chilenas else (agencias[0] if agencias else None)
    slot2 = agencias[0] if agencias else (chilenas[0] if chilenas else None)

    rest_pool = [it for it in chilenas + agencias
                 if slot1 and slot2 and id(it) != id(slot1) and id(it) != id(slot2)]
    rng = _random.Random(seed_str + "|astro")
    rng.shuffle(rest_pool)

    selected: list[NewsItem] = []
    if slot1:
        selected.append(slot1)
    if slot2 and (not slot1 or id(slot2) != id(slot1)):
        selected.append(slot2)
    selected.extend(rest_pool)
    return selected[:TOTAL]


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


def _card_id(it: NewsItem) -> str:
    """Id estable y seguro para anchors publicos por noticia."""
    digest = hashlib.sha256(it.dedup_key.encode("utf-8")).hexdigest()[:12]
    return f"n-{digest}"


def _iso(dt: datetime | None) -> str | None:
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _label(topic: str, topic_labels: dict[str, str]) -> str:
    return topic_labels.get(topic, topic.replace("_", " ").capitalize())


def _tarjeta(it: NewsItem, months: list[str], no_date: str,
             translations: dict | None = None,
             resumenes: dict | None = None,
             is_video: bool = False) -> dict:
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
    card = {
        "id": _card_id(it),
        "url": it.url,
        "title": title,
        "source_name": it.source_name,
        "date": _fecha(it.published, months, no_date),
        "seal_roman": roman,
        "seal_class": clase,
        "seal_color": color,
        "snippet": snippet,
        "image": it.image or f"placeholder-{it.source_id}.png",
        "resumen": resumen,
        "has_resumen": bool(resumen),
        "related": [{"source_name": r["source_name"], "url": r["url"]} for r in rel[:_RELATED_CAP]],
        "related_extra": max(0, len(rel) - _RELATED_CAP),
        "network": network,
        # Las house cards se renderizan como orgánicas (pill de la red, no badge
        # "Sibylla"): así son indistinguibles. `extra["house"]` sigue guiando la
        # selección de slots en `_select_social`.
        "is_house": False,
        "is_video": is_video,
        "video_id": "",
    }
    if is_video:
        card["seal_color"] = "#5EE6E0"
        card["has_resumen"] = False
        card["resumen"] = None
        card["video_id"] = str(it.extra.get("video_id") or "")
    return card


def _puntaje_social_card(card: dict, conteos: dict[str, dict]) -> int:
    """Puntaje social de una tarjeta renderizada: likes - dislikes + 2*comentarios."""
    vals = conteos.get(card.get("id"), {}) if conteos else {}
    return int(vals.get("l", 0) or 0) - int(vals.get("d", 0) or 0) + 2 * int(vals.get("c", 0) or 0)


def _ordenar_cards_por_conteos(cards: list[dict], conteos: dict[str, dict] | None) -> None:
    """Ordena in-place por interacción social, conservando empates editoriales."""
    if not conteos:
        return
    cards.sort(key=lambda c: _puntaje_social_card(c, conteos), reverse=True)


# =============================================================================
# Selección curada de temas (Frontera Digital, Medicina) — 1 tarjeta por fuente
# =============================================================================

# Temas que usan `_select_curado` en vez del corte simple por score. El motor
# temático genérico (rank + diversify, cap 3 por fuente) tiende a dejar las 6
# tarjetas en manos de 1-2 fuentes muy frescas y de alto volumen (ver
# análisis: techcrunch+arxiv copaban Frontera Digital). Aquí se prioriza 1
# fuente distinta por tarjeta antes de repetir ninguna.
CURATED_TOPIC_IDS: set[str] = {"ai", "medicine"}
# Ventana de "fresco" para el relleno: ≤48h (~2 días).
CURATED_FRESH_HOURS = 48.0


def _select_curado(items: list[NewsItem], max_n: int = 6) -> list[NewsItem]:
    """Selecciona `max_n` tarjetas priorizando 1 por fuente distinta.

    1. Separa en FRESCOS (≤ `CURATED_FRESH_HOURS`) y VIEJOS (el resto).
    2. Dentro de cada grupo, arma "rondas": ronda 0 toma el mejor ítem (por
       `_score`) de cada fuente distinta; ronda 1 el segundo de cada fuente,
       etc. Así se agotan las fuentes nuevas antes de repetir ninguna, y solo
       se repite fuente dentro de FRESCOS si hace falta. Si FRESCOS no llega
       a `max_n`, se completa con rondas de VIEJOS (mismo agotamiento por
       fuente) — ahí sí se permite repetir fuente cuanto sea necesario.
    3. Las `max_n` elegidas se ordenan por `_score` puro (portada = importancia
       bruta: un preprint de arXiv puede ir de primero si puntúa más).
    4. Si las 2 primeras tarjetas quedan de la misma fuente, se intercambia la
       segunda por la siguiente de fuente distinta (si existe alguna)."""
    if not items:
        return []

    def _por_fuente_en_rondas(pool: list[NewsItem]) -> list[NewsItem]:
        by_src: dict[str, list[NewsItem]] = {}
        for it in pool:
            by_src.setdefault(it.source_id, []).append(it)
        for lst in by_src.values():
            lst.sort(key=_score, reverse=True)
        out: list[NewsItem] = []
        depth = 0
        while True:
            fila = [lst[depth] for lst in by_src.values() if depth < len(lst)]
            if not fila:
                break
            fila.sort(key=_score, reverse=True)
            out.extend(fila)
            depth += 1
        return out

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=CURATED_FRESH_HOURS)
    frescos = [it for it in items if it.published and it.published >= cutoff]
    fresco_ids = {id(it) for it in frescos}
    viejos = [it for it in items if id(it) not in fresco_ids]

    pool = _por_fuente_en_rondas(frescos) + _por_fuente_en_rondas(viejos)
    seleccion = pool[:max_n]
    seleccion.sort(key=_score, reverse=True)

    if len(seleccion) >= 2 and seleccion[0].source_id == seleccion[1].source_id:
        for i in range(2, len(seleccion)):
            if seleccion[i].source_id != seleccion[0].source_id:
                seleccion[1], seleccion[i] = seleccion[i], seleccion[1]
                break

    return seleccion


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


def _seleccionar_tema(items: list[NewsItem], t: str, max_por_tema: int) -> list[NewsItem]:
    """Ítems de un tema que se muestran como tarjetas (≤ max_por_tema).

    Frontera Digital y Medicina (`CURATED_TOPIC_IDS`) usan `_select_curado`
    (1 tarjeta por fuente, ventana de frescura). Los demás temas usan el corte
    simple: las primeras `max_por_tema`, ya vienen rankeadas por el pipeline."""
    del_tema = [it for it in items if _primario(it) == t]
    if t in CURATED_TOPIC_IDS:
        return _select_curado(del_tema, max_por_tema)
    return del_tema[:max_por_tema]


def _rendered_items(items: list[NewsItem], topics: list[str],
                    max_por_tema: int,
                    social_items: list[NewsItem] | None = None,
                    astro_items: list[NewsItem] | None = None) -> list[NewsItem]:
    """Los NewsItem que se renderizarán como tarjetas (≤ max_por_tema por tema).

    Misma regla de selección que `_agrupar`, pero devuelve los ítems en vez de
    las tarjetas: lo usa la traducción para tocar solo lo visible (estrategia B+A).

    Si se pasan `social_items` o `astro_items`, también se incluyen (siempre
    se renderizan)."""
    salida: list[NewsItem] = []
    for t in _orden_temas(items, topics):
        salida.extend(_seleccionar_tema(items, t, max_por_tema))
    if astro_items:
        salida.extend(astro_items)
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
        seleccion = _seleccionar_tema(items, t, max_por_tema)
        cartas = [_tarjeta(it, months, no_date, translations, resumenes) for it in seleccion]
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


def _build_stellar_titles(
    it: NewsItem,
    translations: dict | None,
    *,
    translate: bool,
    tracker: list[dict] | None = None,
) -> dict[str, str]:
    """Titulos localizados para la tarjeta de Stellar-View.

    Si no hay LLM/cache disponible, cada idioma cae al titulo original para que
    el contrato JSON nunca quede incompleto.
    """
    titles = {lang: it.title for lang in STELLAR_NEWS_LANGS}
    card = {"id": it.dedup_key, "title": it.title, "snippet": _snippet(it.summary)}

    if translations and it.dedup_key in translations:
        titles["es"] = translations[it.dedup_key].get("title") or it.title

    if not translate:
        return titles

    cache = load_cache()
    for lang in STELLAR_NEWS_LANGS:
        if lang == "es" and translations and it.dedup_key in translations:
            continue
        translated = translate_cards([card], lang, cache, tracker=tracker).get(it.dedup_key)
        if translated and translated.get("title"):
            titles[lang] = translated["title"]
    save_cache(cache)
    return titles


def _build_stellar_summary(
    it: NewsItem,
    resumen_es: str | None,
    *,
    translate: bool,
    tracker: list[dict] | None = None,
) -> dict[str, str]:
    """Resumen localizado (es/en/it) para la pantalla de Stellar-View.

    El resumen base en español viene de ``resumen.py``; las traducciones a en/it
    reutilizan ``translate_cards`` con el mismo cache compartido de traducciones.
    El id ``{dedup_key}#sum`` evita colisionar con la entrada del titulo en cache.
    Si no hay resumen base o falla la traduccion, ese idioma se omite y la app
    degrada al español. Nunca rompe el build.
    """
    if not resumen_es:
        return {}
    summary: dict[str, str] = {"es": resumen_es}
    if not translate:
        return summary

    cache = load_cache()
    sum_id = f"{it.dedup_key}#sum"
    for lang in STELLAR_NEWS_LANGS:
        if lang == "es":
            continue
        # El resumen va en "title" porque _parse_response descarta filas sin el.
        card = {"id": sum_id, "title": resumen_es, "snippet": ""}
        got = translate_cards([card], lang, cache, tracker=tracker).get(sum_id)
        if got and got.get("title"):
            summary[lang] = got["title"]
    save_cache(cache)
    return summary


def _stellar_history_path() -> Path:
    return ROOT / "data" / "stellar_history.json"


def _load_stellar_history() -> list[dict]:
    """Lee el historial de destacadas (o [] si no existe o esta corrupto).

    Devuelve una lista de entradas {date, id, source_id}. Nunca lanza: si el
    archivo falta o esta malformado, se degrada a "sin historial" (la primera
    corrida en un host nuevo simplemente elige sin penalizar repeticiones)."""
    try:
        d = json.loads(_stellar_history_path().read_text(encoding="utf-8"))
        entries = d.get("entries", [])
    except Exception:  # noqa: BLE001
        return []
    return [e for e in entries
            if isinstance(e, dict) and e.get("date") and e.get("id")]


def _save_stellar_history(entries: list[dict]) -> Path:
    """Persiste el historial recortado a las ultimas STELLAR_HISTORY_MAX entradas."""
    path = _stellar_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": STELLAR_HISTORY_SCHEMA,
        "entries": entries[-STELLAR_HISTORY_MAX:],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _days_since_featured(dedup_key: str, history: list[dict], today: str) -> int:
    """Dias desde la ultima vez que `dedup_key` fue destacada (grande = mejor).

    `today` y las fechas del historial estan en formato 'YYYY-MM-DD' (UTC).
    Nunca destacada -> sentinela grande para que gane a cualquiera ya mostrada.
    """
    fechas = [e["date"] for e in history
              if e.get("id") == dedup_key and e.get("date")]
    if not fechas:
        return _STELLAR_NEVER_FEATURED
    try:
        last = max(datetime.strptime(f, "%Y-%m-%d").date() for f in fechas)
        hoy = datetime.strptime(today, "%Y-%m-%d").date()
    except ValueError:
        return _STELLAR_NEVER_FEATURED
    return max((hoy - last).days, 0)


def _prev_featured_source(history: list[dict], today: str) -> "str | None":
    """source_id de la destacada mas reciente ANTERIOR a hoy (para variar fuente)."""
    prev = [e for e in history if e.get("date") and e["date"] < today]
    if not prev:
        return None
    prev.sort(key=lambda e: e["date"])
    return prev[-1].get("source_id")


def _record_stellar_featured(
    history: list[dict], today: str, dedup_key: str, source_id: str,
) -> list[dict]:
    """Devuelve el historial con la destacada de hoy anexada (sin duplicar el dia).

    Si ya existia una entrada con fecha `today` (p. ej. la corrida temprana del
    cron), se reemplaza: asi el anclaje intra-dia registra el item vigente sin
    acumular dos entradas del mismo dia."""
    entries = [e for e in history if e.get("date") != today]
    entries.append({"date": today, "id": dedup_key, "source_id": source_id})
    return entries[-STELLAR_HISTORY_MAX:]


def _select_stellar_featured(
    astro_items: list[NewsItem],
    resumenes: dict[str, str],
    *,
    history: list[dict] | None = None,
    today: str | None = None,
) -> "NewsItem | None":
    """Elige el item destacado para Stellar-View.

    Prioridad (de mayor a menor peso):
      1) tiene imagen real,
      2) tiene resumen,
      3) no repetida: mas dias desde la ultima vez que fue destacada
         (nunca destacada = infinito practico),
      4) fuente distinta a la de la destacada anterior,
      5) desempate: mas reciente; a igualdad total, el primero del pool
         (que respeta el orden curado de `_select_astronomia`).

    Imagen y resumen encabezan porque la pantalla de la app los muestra siempre
    que puede; la no-repeticion es penalizacion (no filtro) porque el pool son
    solo 6 tarjetas y a veces repetir es inevitable, y degrada eligiendo la
    menos repetida.

    Anclaje intra-dia: si el historial ya trae una destacada con fecha `today`
    y ese item sigue en el pool, se reutiliza para que una segunda corrida del
    dia (el cron tardio o un workflow_dispatch manual) no cambie lo ya publicado.
    """
    # La tarjeta APOD no es candidata a destacada: Stellar-View ya muestra el APOD
    # directamente (apod-i18n.json); la tercera card de la app espera una *noticia*
    # de astronomía distinta. Este filtro es la guardia de seguridad; el caller
    # también excluye APOD antes de pasar la lista (doble protección).
    astro_items = [it for it in astro_items if it.source_id != APOD_SOURCE_ID]
    if not astro_items:
        return None
    history = history or []
    today = today or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    hoy_ids = {e.get("id") for e in history if e.get("date") == today}
    if hoy_ids:
        for it in astro_items:
            if it.dedup_key in hoy_ids:
                return it

    prev_src = _prev_featured_source(history, today)

    def _recency(it: NewsItem):
        return it.published or datetime.min.replace(tzinfo=timezone.utc)

    def _key(it: NewsItem):
        return (
            bool(it.image),
            it.dedup_key in resumenes,
            _days_since_featured(it.dedup_key, history, today),
            it.source_id != prev_src,
            _recency(it),
        )

    return max(astro_items, key=_key)


def build_stellar_news_payload(
    astro_items: list[NewsItem],
    *,
    site_url: str,
    generated_at: datetime,
    translations: dict | None = None,
    resumenes: dict[str, str] | None = None,
    translate: bool = True,
    tracker: list[dict] | None = None,
    history: list[dict] | None = None,
    today: str | None = None,
) -> dict:
    """Contrato publico que consume Stellar-View.

    Selecciona la noticia destacada priorizando imagen, luego resumen, luego que
    no se haya destacado recientemente (ver ``_select_stellar_featured``). El
    campo ``summary`` es un dict es/en/it con el resumen traducido; puede ser
    ``{}`` si no hay resumen. El campo es opcional/aditivo: versiones antiguas
    de la app lo ignoran.

    Si se pasa ``history`` (lista mutable), se anexa la destacada elegida IN
    PLACE para que el caller persista el historial actualizado con
    ``_save_stellar_history``.
    """
    resumenes = resumenes or {}
    today = today or generated_at.strftime("%Y-%m-%d")
    featured = _select_stellar_featured(
        astro_items, resumenes, history=history, today=today)

    payload = {
        "schema": STELLAR_NEWS_SCHEMA,
        "generated_at": _iso(generated_at),
        "featured": None,
    }
    if featured is None:
        return payload

    anchor = _card_id(featured)
    resumen_es = resumenes.get(featured.dedup_key)
    payload["featured"] = {
        "id": anchor,
        "section": "astronomia",
        "title": _build_stellar_titles(
            featured,
            translations,
            translate=translate,
            tracker=tracker,
        ),
        "summary": _build_stellar_summary(
            featured,
            resumen_es,
            translate=translate,
            tracker=tracker,
        ),
        "image_url": featured.image,
        "has_real_image": bool(featured.image),
        "sibylla_url": f"{site_url}/index.html#{anchor}",
        "original_url": featured.url,
        "canonical_url": featured.canonical_url,
        "source": {
            "id": featured.source_id,
            "name": featured.source_name,
            "tier": featured.tier,
        },
        "published_at": _iso(featured.published),
    }

    # Registrar la destacada del dia en el historial (in place) para que el
    # caller lo persista. Reemplaza la entrada de hoy si ya existia, asi el
    # anclaje intra-dia no acumula duplicados.
    if history is not None:
        history[:] = _record_stellar_featured(
            history, today, featured.dedup_key, featured.source_id)

    return payload


def _write_stellar_news(payload: dict) -> Path:
    out = SITE_DIR / STELLAR_NEWS_FILE
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _write_apod_i18n(payload: dict) -> Path:
    out = SITE_DIR / APOD_I18N_FILE
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def _write_apod_i18n_archive(payload: dict) -> Path:
    """Copia inmutable en `apod-i18n/<fecha>.json`.

    A diferencia de `apod-i18n.json` (que se pisa cada corrida), este archivo
    nunca se reescribe una vez publicado: los workflows lo suben con `scp -r`
    sobre un directorio remoto existente, que MERGEA en vez de purgar, así se
    acumula un archivo por dia sin necesitar el patron descargar/subir que usa
    el historial de metricas (runs.json)."""
    archive_dir = SITE_DIR / APOD_I18N_ARCHIVE_DIR
    archive_dir.mkdir(parents=True, exist_ok=True)
    out = archive_dir / f"{payload['date']}.json"
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def write_apod_sidecar(
    *,
    translate: bool = True,
    tracker: list[dict] | None = None,
    payload: dict | None = None,
) -> Path | None:
    """Genera y escribe `apod-i18n.json` + su copia historica, sin tocar el resto del sitio.

    NASA publica el APOD ~2-3 AM Chile, pero el build completo (`build_all_sites`)
    corre recien a las 11 para que las noticias salgan frescas. Esta funcion la usa
    un cron aparte, mas temprano, para achicar a minutos la ventana en la que
    Stellar-View muestra el APOD de hoy en ingles (ver sibylla/apod.py). Idempotente:
    el build de las 11 vuelve a escribir el mismo archivo sin duplicar trabajo raro
    (y su copia historica, con el mismo nombre de archivo por fecha).

    Si se pasa `payload` (ya construido por `build_all_sites`), se reutiliza sin
    volver a llamar a NASA ni al LLM. Sin `payload` (corrida independiente via
    `--apod-only`), lo obtiene y traduce desde cero."""
    if payload is None:
        # Corrida independiente (--apod-only): carga .env, baja y traduce el APOD.
        # Se auto-suficiente en .env: `--apod-only` llega aqui SIN pasar por
        # run_pipeline (que es quien normalmente carga el .env). En CI no hace falta
        # (las vars ya vienen inyectadas por el workflow), pero en corridas locales
        # sin esto NASA_API_KEY y el proveedor LLM quedan sin configurar.
        load_env()
        apod_data = fetch_apod(get_nasa_api_key())
        if apod_data is None:
            return None
        SITE_DIR.mkdir(parents=True, exist_ok=True)
        payload = build_apod_i18n(apod_data, translate=translate, tracker=tracker)
    else:
        SITE_DIR.mkdir(parents=True, exist_ok=True)
    _write_apod_i18n_archive(payload)
    return _write_apod_i18n(payload)


def build_context(items: list[NewsItem], topics: list[str], meta: dict,
                   lang: str = "es", max_por_tema: int = 6,
                   is_landing: bool = False,
                   translations: dict | None = None,
                   social_items: list[NewsItem] | None = None,
                   astro_items: list[NewsItem] | None = None,
                   divulgacion_items: list[NewsItem] | None = None,
                   sibylla_items: list[NewsItem] | None = None,
                   resumenes: dict | None = None,
                   build_v: int | None = None,
                   social_conteos: dict[str, dict] | None = None) -> dict:
    """Construye el contexto que recibe la plantilla.

    `items` son los ítems normales (temáticos). `social_items` son los ítems
    de redes sociales, `astro_items` los de astronomía — ambos van en sus
    propias secciones al pie de la página.

    `build_v` es la marca de versión del build (epoch UTC). Se planta en el
    HTML (meta `x-build`) y se escribe aparte en `web/build.json`; el cliente
    compara ambos para detectar un redeploy y mostrar el botón "Actualizar".
    Si es None, se calcula aquí (modo de compatibilidad)."""
    tr = load_translations(lang)
    tw = tr["web"]
    months: list[str] = tw["months"]
    no_date: str = tw["no_date"]
    topic_labels: dict[str, str] = tw["topics"]

    ahora = datetime.now(timezone.utc)
    if build_v is None:
        build_v = int(ahora.timestamp())
    # Contar fuentes contando también las sociales.
    all_source_ids = {it.source_id for it in items}
    if social_items:
        all_source_ids |= {it.source_id for it in social_items}
    n_fuentes = len(all_source_ids)
    total_all = len(items) + len(social_items) if social_items else len(items)

    grupos = _agrupar(items, topics, max_por_tema, topic_labels, months, no_date,
                      translations, resumenes)
    for grupo in grupos:
        _ordenar_cards_por_conteos(grupo["cards"], social_conteos)
    observa = t(tr, "web.voice", count=total_all, sources=n_fuentes)
    ts = _instante(ahora, months)
    hero_ts = t(tr, "web.hero_timestamp", date=ts, count=total_all, sources=n_fuentes)

    # Tarjetas de astronomía.
    astro_cards: list[dict] = []
    if astro_items:
        astro_cards = [_tarjeta(it, months, no_date, translations, resumenes) for it in astro_items]
        _ordenar_cards_por_conteos(astro_cards, social_conteos)

    # Tarjetas de divulgación. No se traducen ni resumen: son videos en español.
    divulgacion_cards: list[dict] = []
    if divulgacion_items:
        divulgacion_cards = [_tarjeta(it, months, no_date, is_video=True) for it in divulgacion_items]
        _ordenar_cards_por_conteos(divulgacion_cards, social_conteos)

    # Tarjetas de publicaciones propias (sección SIBYLLA). Ya vienen en español:
    # no se traducen ni piden resumen al LLM; el cuerpo del archivo Markdown
    # hace de resumen (acordeón), pasándolo por el mismo canal `resumenes`.
    sibylla_cards: list[dict] = []
    if sibylla_items:
        pub_bodies = {it.dedup_key: it.extra["body"]
                      for it in sibylla_items if it.extra.get("body")}
        sibylla_cards = [_tarjeta(it, months, no_date, None, pub_bodies) for it in sibylla_items]
        _ordenar_cards_por_conteos(sibylla_cards, social_conteos)

    # Tarjetas sociales.
    social_cards: list[dict] = []
    if social_items:
        social_cards = [_tarjeta(it, months, no_date, translations, resumenes) for it in social_items]
        _ordenar_cards_por_conteos(social_cards, social_conteos)

    # Datos de idioma (el sitio es monolingüe; se conservan por el molde del código).
    lang_label = LANG_LABELS.get(lang, lang.upper())
    lang_options = [(lc, LANG_LABELS[lc]) for lc in ALL_LANGS if lc != lang]
    lang_files = {lc: f"{lc}.html" for lc in ALL_LANGS}

    # Variables SEO.
    site_url = get_site_url()
    page_file = "index.html"  # sitio de una sola página (index.html)
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
        "build_v": build_v,
        "total": total_all,
        "n_fuentes": n_fuentes,
        "grupos": grupos,
        "astro_cards": astro_cards,
        "divulgacion_cards": divulgacion_cards,
        "sibylla_cards": sibylla_cards,
        "social_cards": social_cards,
        "social_conteos": social_conteos or {},
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
                astro_items: list[NewsItem] | None = None,
                divulgacion_items: list[NewsItem] | None = None,
                sibylla_items: list[NewsItem] | None = None,
                resumenes: dict | None = None,
                build_v: int | None = None,
                social_conteos: dict[str, dict] | None = None) -> str:
    """Renderiza la portada a una cadena HTML."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    tmpl = env.get_template("index.html.j2")
    return tmpl.render(**build_context(items, topics, meta, lang, max_por_tema,
                                        is_landing, translations, social_items,
                                        astro_items, divulgacion_items,
                                        sibylla_items, resumenes, build_v,
                                        social_conteos))


def _render_pub_page(it: NewsItem, site_url: str, build_v: int) -> str:
    """Renderiza la página individual de una publicación propia (`pub/<slug>.html`).

    La tarjeta SIBYLLA enlaza aquí cuando la publicación no lleva `url` externa.
    Es una página estática autocontenida (CSS inline, español): el cuerpo del
    archivo Markdown es el contenido, y `volver` enlaza a la portada. Reutiliza
    el sello de tier y el formato de fecha de las tarjetas."""
    tw = load_translations("es")["web"]
    roman, _clase, color = _SEAL.get(it.tier, _SEAL[3])
    slug = it.extra.get("slug") or _card_id(it)
    # Los assets estáticos viven en la raíz del sitio; la página, en pub/.
    image = it.image
    if image and not image.startswith(("http://", "https://")):
        image = f"../{image}"
    og_image = (it.image if it.image and it.image.startswith(("http://", "https://"))
                else f"{site_url}/icon-512.png")
    ctx = {
        "lang": "es",
        "title": it.title,
        "description": (it.summary or "")[:160] or None,
        "canonical": f"{site_url}/pub/{slug}.html",
        "og_image": og_image,
        "build_v": build_v,
        "volver": tw.get("pub_volver", "Volver al inicio"),
        "source_name": it.source_name,
        "date_str": _fecha(it.published, tw["months"], tw["no_date"]),
        "seal_color": color,
        "seal_roman": roman,
        "resumen": it.summary or "",
        "image": image,  # None si sin imagen (la plantilla omite el <img>)
        "body": it.extra.get("body", ""),
        "site_motto": tw.get("footer_motto", ""),
    }
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True, lstrip_blocks=True,
    )
    return env.get_template("pub.html.j2").render(**ctx)


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
                        social_items: list[NewsItem] | None = None,
                        astro_items: list[NewsItem] | None = None) -> dict[str, dict]:
    """Traduce las tarjetas RENDERIZADAS a cada idioma (estrategia B+A).

    Devuelve {lang: {dedup_key: {title, snippet}}}. Usa y actualiza el cache en
    disco (`data/translations.json`). Sin LLM configurado, los mapas quedan
    vacíos y las tarjetas caen a su idioma original aguas abajo.

    Si se pasa un `tracker`, se apendea el uso de tokens de cada llamada LLM.
    `social_items` y `astro_items` se incluyen siempre en el lote de traducción."""
    rendered = _rendered_items(items, topics, max_por_tema, social_items, astro_items)
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


def _write_sitemap(site_url: str, lastmod: str,
                   extra_locs: list[str] | None = None) -> Path:
    """Escribe web/sitemap.xml: la portada (index.html) + una entrada por cada
    página de publicación propia generada en `pub/` (si las hay)."""
    entries = [f"    <loc>{site_url}/index.html</loc>\n"
               f"    <lastmod>{lastmod}</lastmod>\n"
               f"    <changefreq>hourly</changefreq>\n"
               f"    <priority>1.0</priority>\n"]
    for loc in (extra_locs or []):
        entries.append(f"    <loc>{loc}</loc>\n"
                       f"    <lastmod>{lastmod}</lastmod>\n"
                       f"    <changefreq>weekly</changefreq>\n"
                       f"    <priority>0.6</priority>\n")
    body = "\n".join(f"  <url>\n{e}  </url>" for e in entries)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
           f"{body}\n"
           "</urlset>\n")
    out = SITE_DIR / "sitemap.xml"
    out.write_text(xml, encoding="utf-8")
    return out


def _write_build_meta(build_v: int) -> Path:
    """Escribe web/build.json con la marca de versión del build.

    El cliente compara este `v` (vivo, cache-busteado por URL) con el `v`
    embebido en index.html (meta `x-build`). Si difieren y el remoto es mayor,
    se muestra el botón "Actualizar". Archivo minúsculo (~20 bytes)."""
    out = SITE_DIR / "build.json"
    out.write_text(json.dumps({"v": build_v}, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    return out


def build_all_sites(items: list[NewsItem], topics: list[str], meta: dict,
                     max_por_tema: int = 6, translate: bool = True,
                     translate_tracker: list[dict] | None = None,
                     include_x: bool = False) -> list[Path]:
    """Genera un HTML por idioma + index.html de aterrizaje con auto-detección.

    Estructura generada (sitio monolingüe, español):
        web/index.html  → la única página del sitio (español)

    Si `translate` es True y hay LLM configurado, el título y snippet de las
    tarjetas en otros idiomas se traducen al español; las que ya están en
    español se devuelven iguales.

    Si se pasa `translate_tracker`, se apendea el uso de tokens de cada llamada LLM.

    Los ítems de redes sociales (X, Mastodon, Bluesky…) se extraen de la lista principal
    y se muestran en su propia sección al pie de la página ("Voces de la red")."""
    _assert_min_items(items)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    # Separar ítems: normales / astronomía / divulgación / redes sociales.
    normal_items = [it for it in items
                    if not _is_social(it) and not _is_astro(it) and not _is_divulgacion(it)]
    astro_raw = [it for it in items if _is_astro(it)]
    divulgacion_raw = [it for it in items if _is_divulgacion(it)]
    social_raw = [it for it in items if _is_social(it)]

    # Seleccionar 6 tarjetas de astronomía (sin el APOD aún: se inyecta después).
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    astro_top = _select_astronomia(astro_raw, today) if astro_raw else []
    divulgacion_top = _select_divulgacion(divulgacion_raw) if divulgacion_raw else []
    if "divulgacion" in topics:
        log.info("Divulgación: %s videos recibidos, %s tarjetas seleccionadas",
                 len(divulgacion_raw), len(divulgacion_top))

    # APOD del día: se descarga una vez aquí y se reutiliza para (a) la tarjeta
    # de la sección Astronomía y (b) apod-i18n.json (sidecar de Stellar-View).
    # La traducción comparte el cache translations.json con el resto del build.
    apod_data = fetch_apod(get_nasa_api_key())
    apod_payload = None
    apod_card = None
    if apod_data is not None:
        apod_payload = build_apod_i18n(apod_data, translate=translate, tracker=translate_tracker)
        apod_card = build_apod_card(apod_data, apod_payload)

    # Fetch house posts (cuentas propias de Sibylla) y seleccionar 6 tarjetas.
    from .fetchers import fetch_house_posts
    sc = load_social_config()
    house_accounts = sc.get("house_accounts", [])
    house_items = fetch_house_posts(house_accounts, include_x=include_x)
    social_top = _select_social(social_raw, house_items, sc, today)

    # Publicaciones propias (sección SIBYLLA): archivos Markdown versionados en
    # publicaciones/, sin red ni LLM. Sin publicaciones, la sección no existe.
    from .publicaciones import load_publicaciones
    sibylla_items = load_publicaciones()

    # Traducción de tarjetas (título + snippet) al español si hay LLM.
    # El APOD NO entra aquí: ya viene traducido de build_apod_i18n (mismo LLM,
    # mismo cache); inyectarlo aquí gastaría tokens de más para el mismo texto.
    translations = None
    if translate:
        by_lang = _build_translations(normal_items, topics, max_por_tema,
                                      tracker=translate_tracker,
                                      social_items=social_top,
                                      astro_items=astro_top)
        translations = by_lang.get("es")

    # Resúmenes en español de las tarjetas renderizadas (botón "Resumen" + acordeón).
    # Igual que con traducciones: el APOD se inyecta después con su explicación ES.
    from .resumen import build_resumenes
    rendered_for_resumen = _rendered_items(normal_items, topics, max_por_tema,
                                           social_top, astro_top)
    resumenes = build_resumenes(rendered_for_resumen, tracker=translate_tracker)

    # Inyectar tarjeta APOD en la sección Astronomía: reemplaza la más antigua,
    # o rellena si hay menos de 6. La sección de Stellar-View recibe las tarjetas
    # SIN el APOD (la app ya muestra el APOD aparte vía apod-i18n.json; la
    # tercera card de la app espera una noticia distinta).
    if apod_card is not None:
        if len(astro_top) >= ASTRO_MAX_TOTAL:
            oldest_i = min(
                range(len(astro_top)),
                key=lambda i: astro_top[i].published or datetime.min.replace(tzinfo=timezone.utc),
            )
            log.info(
                "APOD: reemplaza '%s' (%s) en la sección Astronomía.",
                astro_top[oldest_i].source_id,
                astro_top[oldest_i].published,
            )
            astro_top[oldest_i] = apod_card
        else:
            astro_top.append(apod_card)
        # Inyectar traducción y resumen en ES desde el payload ya construido
        # (cero llamadas LLM extra; si no hay ES, la tarjeta cae al inglés original).
        if apod_payload:
            es_title = apod_payload["title"].get("es")
            es_explanation = apod_payload["explanation"].get("es")
            if es_title:
                translations = translations or {}
                translations[apod_card.dedup_key] = {
                    "title": es_title,
                    "snippet": _snippet(es_explanation or ""),
                }
            if es_explanation:
                resumenes[apod_card.dedup_key] = es_explanation

    # Conteos sociales pre-agregados (1 lectura REST pública). Si falla, el
    # orden editorial queda intacto y el cliente hidrata lo que pueda.
    from .social_sync import fetch_conteos
    social_conteos = fetch_conteos()

    # Marca de build (única fuente de verdad): se planta en el HTML (meta
    # x-build) y en web/build.json; el cliente compara ambas para detectar un
    # redeploy y mostrar el botón "Actualizar".
    ahora = datetime.now(timezone.utc)
    build_v = int(ahora.timestamp())
    site_url = get_site_url()

    # Páginas propias de las publicaciones SIBYLLA: las que NO llevan `url`
    # externa reciben una página estática en web/pub/<slug>.html y la tarjeta
    # enlaza ahí (mutando `it.url` antes de render_html). Las que ya traen
    # `url` externa no generan página (gana esa). Las URLs se añaden al sitemap.
    pub_locs: list[str] = []
    if sibylla_items:
        pub_dir = SITE_DIR / "pub"
        pub_dir.mkdir(parents=True, exist_ok=True)
        for it in sibylla_items:
            if it.url:
                continue
            slug = it.extra.get("slug") or _card_id(it)
            pub_html = _render_pub_page(it, site_url, build_v)
            pub_path = pub_dir / f"{slug}.html"
            pub_path.write_text(pub_html, encoding="utf-8")
            paths.append(pub_path)
            it.url = f"pub/{slug}.html"
            pub_locs.append(f"{site_url}/pub/{slug}.html")

    # Única página del sitio (español).
    html = render_html(normal_items, topics, meta, lang="es", max_por_tema=max_por_tema,
                        is_landing=False, translations=translations,
                        social_items=social_top, astro_items=astro_top,
                        divulgacion_items=divulgacion_top,
                        sibylla_items=sibylla_items,
                        resumenes=resumenes, build_v=build_v,
                        social_conteos=social_conteos)
    out = SITE_DIR / "index.html"
    out.write_text(html, encoding="utf-8")
    paths.append(out)

    # build.json: marca viva que consulta el cliente (se cache-bustea por URL).
    paths.append(_write_build_meta(build_v))

    # JSON publico para Stellar-View: una noticia destacada de Astronomia.
    # El historial (data/stellar_history.json) se persiste en el host, NO se
    # publica a la web: evita repetir la misma noticia dia tras dia. Si no
    # existe (host nuevo), arranca vacio y no penaliza repeticiones.
    stellar_history = _load_stellar_history()
    # Stellar-View recibe las tarjetas de astronomía SIN el APOD: la app ya
    # muestra la foto del día vía apod-i18n.json; su tercera card espera una
    # noticia distinta. _select_stellar_featured lleva además su propia guardia.
    stellar_astro = [it for it in astro_top if it.source_id != APOD_SOURCE_ID]
    stellar_payload = build_stellar_news_payload(
        stellar_astro,
        site_url=site_url,
        generated_at=ahora,
        translations=translations,
        resumenes=resumenes,
        translate=translate,
        tracker=translate_tracker,
        history=stellar_history,
        today=today,
    )
    paths.append(_write_stellar_news(stellar_payload))
    # Persistido aparte de `paths` (que son salidas de web/): este archivo vive
    # en data/ y lo sube el workflow por SSH junto a x_recent.json.
    _save_stellar_history(stellar_history)

    # JSON público para Stellar-View: traducción es/it del APOD de HOY, más su
    # copia histórica en apod-i18n/<fecha>.json (ver sibylla/apod.py). Se
    # reutiliza el payload ya construido arriba (sin llamar de nuevo a NASA ni
    # al LLM). Si la API de NASA falló, apod_payload es None y write_apod_sidecar
    # lo reintenta desde cero (puede salir bien si fue un fallo transitorio).
    # Normalmente el cron temprano de regenerate-apod.yml ya dejó escrito el
    # archivo; esto lo re-escribe (idempotente). Nunca rompe el build.
    apod_path = write_apod_sidecar(
        translate=translate, tracker=translate_tracker, payload=apod_payload
    )
    if apod_path is not None:
        paths.append(apod_path)

    # Assets estáticos (favicons, iconos, manifest) → se publican en la raíz.
    paths.extend(_copy_static_assets())

    # Archivos SEO auxiliares.
    paths.append(_write_robots(site_url))
    paths.append(_write_sitemap(site_url, ahora.strftime("%Y-%m-%d"), pub_locs))

    return paths
