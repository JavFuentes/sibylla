"""Gestión de canales de YouTube para la sección Divulgación.

Funciones puras (sobre strings) para editar ``config/sources.yaml`` y la lista
``DEFAULT_FREE_SOURCES`` de ``sibylla/pipeline.py``, más wrappers de red/E-S
que orquestan el alta/baja desde la herramienta admin (``sibylla.admin``).

Los cambios llegan a producción solo tras commit+push (el cron de CI regenera
el sitio desde el repo). El dashboard admin **solo edita archivos** y avisa de
cambios pendientes: nunca commitea ni pushea.
"""
from __future__ import annotations

import logging
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import requests
import yaml

from .config import CONFIG_PATH, ROOT, get_youtube_api_key, load_registry
from .fetchers import _YT_UA, safe_error

log = logging.getLogger("sibylla")

PIPELINE_PATH = ROOT / "sibylla" / "pipeline.py"

# --- Constantes -------------------------------------------------------------

# Channel ID de YouTube: 'UC' + 22 chars del alfabeto URL-safe de YouTube.
_CANAL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
# Marcador embebido por YouTube en el HTML del canal: '"channelId":"UCxxx"'.
_CHANNEL_ID_HTML_RE = re.compile(r'"channelId":"(UC[0-9A-Za-z_-]{22})"')
# og:title para extraer el nombre público del canal desde el HTML.
_OG_TITLE_RE = re.compile(
    r'<meta\s+property="og:title"\s+content="([^"]*)"',
    re.IGNORECASE,
)

_ENDPOINT_CHANNELS = "https://www.googleapis.com/youtube/v3/channels"
_ENDPOINT_PLAYLIST_ITEMS = "https://www.googleapis.com/youtube/v3/playlistItems"
_FEED_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
_CANAL_URL = "https://www.youtube.com/{handle}"

_LICENSE = "solo miniatura + título + enlace al video"


# --- Utilidades -------------------------------------------------------------


def _strip_accents(s: str) -> str:
    """ASCII-fold: elimina tildes y diacríticos combinantes (ñ→n, é→e, …)."""
    return "".join(
        c for c in unicodedata.normalize("NFD", s)
        if unicodedata.category(c) != "Mn"
    )


def _extraer_channel_id(source) -> str:
    """channel_id (UC...) de una fuente: del campo YAML `channel_id` o del query
    param de la URL del feed. Misma lógica que ``_yt_channel_id`` en fetchers."""
    cid = str(source.raw.get("channel_id") or "").strip()
    if cid:
        return cid
    try:
        return (parse_qs(urlparse(source.url or "").query).get("channel_id") or [""])[0].strip()
    except Exception:  # noqa: BLE001
        return ""


# --- Parseo de la entrada del usuario ---------------------------------------


def parsear_entrada(texto: str) -> dict:
    """Normaliza la entrada del usuario a ``{"tipo": ..., "valor": ...}``.

    Acepta:
      - channel_id directo (``UCxxxxxxxxxxxxxxxxxxxxxx``)
      - URL ``/channel/UC...``
      - URL ``/@handle`` o ``@handle`` suelto
      - ``handle`` pelado (sin @)

    Tipos devueltos: ``"channel_id"`` (valor = UC…) o ``"handle"`` (valor = @handle).
    """
    s = (texto or "").strip()
    if not s:
        raise ValueError("entrada vacía")

    # channel_id pelado
    if _CANAL_ID_RE.fullmatch(s):
        return {"tipo": "channel_id", "valor": s}

    # URL de YouTube
    if "youtube.com" in s or "youtu.be" in s:
        m = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", s)
        if m:
            return {"tipo": "channel_id", "valor": m.group(1)}
        m = re.search(r"/(@[A-Za-z0-9._-]+)", s)
        if m:
            return {"tipo": "handle", "valor": m.group(1)}
        m = re.search(r"/(?:user|c)/([^/?#]+)", s)
        if m:
            return {"tipo": "handle", "valor": "@" + m.group(1)}
        raise ValueError(f"URL de YouTube no reconocida: {s}")

    # URL de otro sitio: rechazar (no es ni channel_id ni handle de YouTube)
    if "://" in s or s.startswith("//"):
        raise ValueError(f"no es una URL de YouTube ni un handle: {s}")

    # @handle suelto
    if s.startswith("@"):
        return {"tipo": "handle", "valor": s}

    # handle pelado (sin @)
    return {"tipo": "handle", "valor": "@" + s}


# --- Resolución y verificación (red) ----------------------------------------


def resolver_channel_id(entrada: dict, api_key: str = "") -> tuple[str, str | None]:
    """Resuelve la entrada a ``(channel_id, titulo)``.

    ``titulo`` es ``None`` si no se consultó la API (channel_id directo o
    scraping sin og:title). Lanza ``ValueError`` si no se puede resolver.
    """
    if entrada["tipo"] == "channel_id":
        cid = entrada["valor"]
        if api_key:
            try:
                r = requests.get(_ENDPOINT_CHANNELS, timeout=20, params={
                    "part": "snippet",
                    "id": cid,
                    "key": api_key,
                })
                if r.status_code == 200:
                    items = (r.json().get("items") or [])
                    if items:
                        titulo = (items[0].get("snippet") or {}).get("title")
                        return cid, titulo
            except Exception as exc:  # noqa: BLE001
                log.warning("resolver_channel_id: API por id falló (%s); uso UC directo",
                            safe_error(exc))
        return cid, None

    handle = entrada["valor"]
    handle = handle if handle.startswith("@") else "@" + handle

    if api_key:
        try:
            r = requests.get(_ENDPOINT_CHANNELS, timeout=20, params={
                "part": "snippet",
                "forHandle": handle,
                "key": api_key,
            })
            if r.status_code == 200:
                items = (r.json().get("items") or [])
                if items:
                    cid = items[0]["id"]
                    titulo = (items[0].get("snippet") or {}).get("title")
                    return cid, titulo
            else:
                log.warning("resolver_channel_id: API %d para %s; cae a scraping",
                            r.status_code, handle)
        except Exception as exc:  # noqa: BLE001
            log.warning("resolver_channel_id: API falló (%s); cae a scraping",
                        safe_error(exc))

    # Fallback: scraping del HTML del canal
    try:
        r = requests.get(_CANAL_URL.format(handle=handle), timeout=20, headers={
            "User-Agent": _YT_UA, "Accept-Language": "es-ES,es;q=0.9",
        })
        r.raise_for_status()
        m_cid = _CHANNEL_ID_HTML_RE.search(r.text)
        if m_cid:
            m_title = _OG_TITLE_RE.search(r.text)
            titulo = m_title.group(1) if m_title else None
            return m_cid.group(1), titulo
    except Exception as exc:  # noqa: BLE001
        log.warning("resolver_channel_id: scraping falló para %s (%s)",
                    handle, safe_error(exc))

    raise ValueError(f"no se pudo resolver el canal '{handle}' "
                     "(¿existe? ¿lo escribió bien?)")


def verificar_canal(channel_id: str, api_key: str = "") -> bool:
    """True si el canal tiene ≥1 video público.

    Con API key: ``playlistItems`` sobre la playlist de uploads (truco UC→UU,
    1 unidad de cuota). Sin key: feed Atom del canal.
    """
    if not channel_id.startswith("UC"):
        return False

    if api_key:
        uploads = "UU" + channel_id[2:]
        try:
            r = requests.get(_ENDPOINT_PLAYLIST_ITEMS, timeout=20, params={
                "part": "snippet",
                "playlistId": uploads,
                "maxResults": 1,
                "key": api_key,
            })
            if r.status_code == 200:
                return len((r.json().get("items") or [])) > 0
            log.warning("verificar_canal: API %d; cae a feed", r.status_code)
        except Exception as exc:  # noqa: BLE001
            log.warning("verificar_canal: API falló (%s); cae a feed", safe_error(exc))

    try:
        r = requests.get(_FEED_URL.format(cid=channel_id), timeout=20, headers={
            "User-Agent": _YT_UA, "Accept-Language": "es-ES,es;q=0.9",
        })
        r.raise_for_status()
        feed = feedparser.parse(r.content)
        return len(feed.entries) > 0
    except Exception as exc:  # noqa: BLE001
        log.warning("verificar_canal: feed falló para %s (%s)",
                    channel_id, safe_error(exc))
        return False


# --- Generación de id -------------------------------------------------------


def generar_id(handle: str, ids_existentes: set[str]) -> str:
    """``yt_`` + slug ascii minúsculas. Lanza ``ValueError`` si colisiona."""
    h = handle.lstrip("@").strip()
    h = _strip_accents(h)
    slug = re.sub(r"[^A-Za-z0-9]+", "_", h).strip("_").lower()
    if not slug:
        raise ValueError(f"no se pudo generar un id válido desde '{handle}'")
    nuevo_id = f"yt_{slug}"
    if nuevo_id in ids_existentes:
        raise ValueError(f"el id '{nuevo_id}' ya existe "
                         "(usa otro nombre o renombra el canal existente)")
    return nuevo_id


# --- Bloque YAML canónico ---------------------------------------------------


def _bloque_canal(datos: dict) -> str:
    """Genera el texto YAML de un bloque ``yt_*`` con el formato canónico
    (idéntico al de los canales curados a mano en sources.yaml)."""
    lineas = [
        f'  - id: {datos["id"]}',
        f'    name: "{datos["name"]}"',
        '    publisher: "YouTube"',
        '    tier: 3',
        '    type: rss',
        '    category: youtube',
        f'    handle: "{datos["handle"]}"',
        f'    url: "{datos["url"]}"',
        '    topics: [divulgacion]',
        '    lang: es',
        f'    license: "{_LICENSE}"',
        '    access: open',
        '    cost: free',
        f'    status: {datos["status"]}',
    ]
    if datos.get("notes"):
        lineas.append(f'    notes: "{datos["notes"]}"')
    return "\n".join(lineas)


# --- Cirugía sobre sources.yaml (strings) -----------------------------------


def agregar_canal_yaml(texto: str, datos: dict) -> str:
    """Inserta el bloque nuevo **tras el último** ``yt_*`` de sources.yaml.

    Siempre cirugía de texto: nunca ``yaml.dump`` (el archivo está curado a
    mano con comentarios que un round-trip destruiría).
    """
    lineas = texto.split("\n")
    ultimo_yt = -1
    for i, l in enumerate(lineas):
        if l.startswith("  - id: yt_"):
            ultimo_yt = i
    if ultimo_yt == -1:
        raise ValueError("no se encontró ningún bloque yt_* en sources.yaml")

    # Final del bloque del último yt_*: primera línea que no empieza con 4
    # espacios (campo indentado del bloque).
    j = ultimo_yt + 1
    while j < len(lineas) and lineas[j].startswith("    "):
        j += 1

    # Línea en blanco inicial: separa el bloque nuevo del canal anterior, como
    # la convención del archivo curado a mano (todos los bloques van separados
    # por una línea vacía). La baja deduplica blancos, así que alta+baja siguen
    # siendo inversas exactas.
    bloque = [""] + _bloque_canal(datos).split("\n")
    # Asegurar separación con la siguiente sección si no hay línea en blanco.
    if j < len(lineas) and lineas[j].strip() != "" and not lineas[j].startswith("  - id:"):
        bloque.append("")

    return "\n".join(lineas[:j] + bloque + lineas[j:])


def quitar_canal_yaml(texto: str, canal_id: str) -> str:
    """Elimina el bloque del ``canal_id`` de sources.yaml."""
    lineas = texto.split("\n")
    inicio = -1
    for i, l in enumerate(lineas):
        if l.strip() == f"- id: {canal_id}":
            inicio = i
            break
    if inicio == -1:
        raise ValueError(f"no se encontró el canal '{canal_id}' en sources.yaml")

    # Final del bloque: siguiente `  - id:`, cabecera de sección (`#`) o
    # clave top-level (no indentada y no vacía). Las líneas en blanco entre
    # el bloque y el límite NO se cuentan como parte del bloque.
    j = inicio + 1
    while j < len(lineas):
        l = lineas[j]
        if l.startswith("  - id:") or l.startswith("#"):
            break
        if l and not l.startswith(" "):
            break
        j += 1
    # Retroceder líneas en blanco finales (conservar el separador entre el
    # bloque anterior y el siguiente límite -> alta+baja son inversas).
    while j > inicio + 1 and lineas[j - 1].strip() == "":
        j -= 1

    nuevas = lineas[:inicio] + lineas[j:]
    # Evitar dos líneas en blanco consecutivas en el punto de borrado.
    if (inicio > 0 and inicio < len(nuevas)
            and nuevas[inicio - 1] == "" and nuevas[inicio] == ""):
        nuevas = nuevas[:inicio] + nuevas[inicio + 1:]
    return "\n".join(nuevas)


# --- Cirugía sobre pipeline.py (strings) ------------------------------------


def _localizar_bloque_pipeline(lineas: list[str]) -> tuple[int, int]:
    """Devuelve ``(inicio, fin)`` (índices exclusivos del contenido) de
    DEFAULT_FREE_SOURCES. Lanza ValueError si no se encuentra."""
    inicio_bloque = -1
    for i, l in enumerate(lineas):
        if "DEFAULT_FREE_SOURCES" in l and "=" in l and "[" in l:
            inicio_bloque = i + 1
            break
    if inicio_bloque == -1:
        raise ValueError("no se encontró DEFAULT_FREE_SOURCES en pipeline.py")
    fin_bloque = -1
    for j in range(inicio_bloque, len(lineas)):
        if lineas[j].rstrip().endswith("]"):
            fin_bloque = j
            break
    if fin_bloque == -1:
        raise ValueError("no se encontró el cierre ']' de DEFAULT_FREE_SOURCES")
    return inicio_bloque, fin_bloque


def agregar_a_pipeline(texto: str, canal_id: str) -> str:
    """Inserta ``"canal_id",`` tras la última línea con ``"yt_`` dentro de
    DEFAULT_FREE_SOURCES."""
    lineas = texto.split("\n")
    inicio, fin = _localizar_bloque_pipeline(lineas)
    ultimo_yt = -1
    for k in range(inicio, fin):
        if '"yt_' in lineas[k]:
            ultimo_yt = k
    if ultimo_yt == -1:
        raise ValueError("no se encontró ningún \"yt_ en DEFAULT_FREE_SOURCES")
    nuevo = f'    "{canal_id}",'
    return "\n".join(lineas[:ultimo_yt + 1] + [nuevo] + lineas[ultimo_yt + 1:])


def quitar_de_pipeline(texto: str, canal_id: str) -> str:
    """Elimina el token ``"canal_id"`` (con coma opcional) de DEFAULT_FREE_SOURCES."""
    lineas = texto.split("\n")
    inicio, fin = _localizar_bloque_pipeline(lineas)
    token_re = re.compile(rf'"{re.escape(canal_id)}",?\s*')
    for k in range(inicio, fin):
        if f'"{canal_id}"' in lineas[k]:
            limpiada = token_re.sub("", lineas[k])
            if limpiada.strip() == "":
                lineas.pop(k)
            else:
                lineas[k] = limpiada
            break
    return "\n".join(lineas)


# --- Wrappers de E/S (disco real) -------------------------------------------


def listar_canales(path: Path = CONFIG_PATH) -> list[dict]:
    """Lista los canales yt_* con sus metadatos."""
    _, sources = load_registry(path)
    out = []
    for s in sources:
        if s.raw.get("category") == "youtube":
            out.append({
                "id": s.id,
                "name": s.name,
                "handle": s.raw.get("handle", ""),
                "channel_id": _extraer_channel_id(s),
                "status": s.raw.get("status", ""),
                "notes": s.raw.get("notes", "") or "",
                "url": s.url or "",
            })
    return out


def estado_git() -> bool:
    """True si ``config/sources.yaml`` o ``sibylla/pipeline.py`` tienen cambios
    sin commitear (banner de 'cambios pendientes')."""
    try:
        r = subprocess.run(
            ["git", "status", "--porcelain", "--",
             "config/sources.yaml", "sibylla/pipeline.py"],
            capture_output=True, text=True, cwd=str(ROOT), timeout=10,
        )
        return bool((r.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return False


def alta_canal(entrada_texto: str, nombre: str | None = None) -> tuple[bool, str]:
    """Alta completa de un canal: parsear → resolver → verificar → cirugías →
    escribir. Devuelve ``(exito, mensaje)``. Nunca deja el repo a medio editar.
    """
    try:
        yaml_actual = CONFIG_PATH.read_text(encoding="utf-8")
        py_actual = PIPELINE_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudieron leer los archivos: {safe_error(exc)}"

    api_key = get_youtube_api_key()

    try:
        entrada = parsear_entrada(entrada_texto)
    except ValueError as exc:
        return False, str(exc)

    try:
        cid, titulo = resolver_channel_id(entrada, api_key)
    except ValueError as exc:
        return False, str(exc)

    if not verificar_canal(cid, api_key):
        return False, (f"el canal {cid} no tiene videos públicos accesibles "
                       "(¿canal nuevo sin uploads? ¿YouTube bloqueó el acceso?)")

    _, sources = load_registry()
    ids_existentes = {s.id for s in sources}

    # Rechazar duplicado por channel_id
    for s in sources:
        if s.raw.get("category") == "youtube" and _extraer_channel_id(s) == cid:
            return False, f"ya existe un canal con channel_id {cid} ({s.id})"

    handle = entrada["valor"] if entrada["tipo"] == "handle" else ""
    id_base = handle or (nombre or "").strip() or titulo or cid
    try:
        nuevo_id = generar_id(id_base, ids_existentes)
    except ValueError as exc:
        return False, str(exc)

    name = (nombre or "").strip() or titulo or handle.lstrip("@") or cid

    datos = {
        "id": nuevo_id,
        "name": name,
        "handle": handle,
        "channel_id": cid,
        "url": _FEED_URL.format(cid=cid),
        "status": f"verified_{date.today().isoformat()}",
    }

    try:
        yaml_nuevo = agregar_canal_yaml(yaml_actual, datos)
        py_nuevo = agregar_a_pipeline(py_actual, nuevo_id)
    except ValueError as exc:
        return False, str(exc)

    # Smoke test: el YAML resultante debe parsear y el pipeline contener el id.
    try:
        yaml.safe_load(yaml_nuevo)
    except yaml.YAMLError as exc:
        return False, f"el YAML resultante no parsea (no se ha tocado disco): {exc}"
    if f'"{nuevo_id}"' not in py_nuevo:
        return False, "el pipeline no contiene el id nuevo (no se ha tocado disco)"

    CONFIG_PATH.write_text(yaml_nuevo, encoding="utf-8")
    PIPELINE_PATH.write_text(py_nuevo, encoding="utf-8")
    return True, f"canal '{name}' agregado como {nuevo_id} (channel_id {cid}). " \
                 f"Commitea y pushea para que llegue al sitio."


def baja_canal(canal_id: str) -> tuple[bool, str]:
    """Baja de un canal: cirugía inversa + smoke test. Devuelve ``(exito, msg)``."""
    canal_id = (canal_id or "").strip()
    if not canal_id.startswith("yt_"):
        return False, f"id inválido: '{canal_id}' (debe empezar por 'yt_')"

    try:
        yaml_actual = CONFIG_PATH.read_text(encoding="utf-8")
        py_actual = PIPELINE_PATH.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return False, f"no se pudieron leer los archivos: {safe_error(exc)}"

    try:
        yaml_nuevo = quitar_canal_yaml(yaml_actual, canal_id)
        py_nuevo = quitar_de_pipeline(py_actual, canal_id)
    except ValueError as exc:
        return False, str(exc)

    try:
        yaml.safe_load(yaml_nuevo)
    except yaml.YAMLError as exc:
        return False, f"el YAML resultante no parsea (no se ha tocado disco): {exc}"
    if f'"{canal_id}"' in py_nuevo:
        return False, f"el id {canal_id} sigue en el pipeline (no se ha tocado disco)"

    CONFIG_PATH.write_text(yaml_nuevo, encoding="utf-8")
    PIPELINE_PATH.write_text(py_nuevo, encoding="utf-8")
    return True, f"canal {canal_id} eliminado. " \
                 f"Commitea y pushea para que llegue al sitio."
