"""Construcción y envío del boletín diario de Sibylla.

El build escribe una edición global. El paso posterior al deploy la reparte
según los temas de cada suscriptor y persiste el avance por uid tras cada
destinatario. Todos los fallos son aislados: este módulo nunca debe poner en
riesgo la publicación del sitio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import smtplib
import ssl
import textwrap
import time
from typing import Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, get_site_url, load_env
from .i18n import load_translations, t
from .suscriptores import Suscriptor

log = logging.getLogger("sibylla")

# Debe coincidir con TEMAS_VALIDOS de suscriptores.py, temasBoletin() de
# firestore.rules y TEMAS_BOLETIN de static/social.js.
TEMAS_VALIDOS = ("nacional", "ai", "medicine", "astronomia", "divulgacion")
SECCION_FIJA = "sibylla"
MAX_POR_TEMA = 3
MAX_TARJETAS = 12
MAX_BREVES = 4
# Antigüedad máxima de una señal breve noticiosa, en días de calendario de
# Santiago. Los vídeos y las publicaciones propias quedan exentos: son
# atemporales y su sección ya aplica su propia ventana en web.py.
MAX_DIAS_BREVE = 7
MAX_RESUMEN_DESTACADA = 1500
MAX_ASUNTO = 65
CAMPOS_TARJETA = (
    "id", "url", "title", "source_name", "date", "published", "snippet", "resumen",
    "seal_roman", "is_video", "es_espanol",
)
EDICION_PATH = ROOT / "data" / "newsletter_edicion.json"
ESTADO_PATH = ROOT / "data" / "newsletter_state.json"
# v3 añade `published` (ISO 8601 UTC) y `es_espanol` a cada tarjeta. Sin período
# de compatibilidad: el workflow construye y envía en la misma corrida, y
# `cargar_edicion()` rechaza cualquier otro esquema.
EDICION_SCHEMA = "cl.sibylla.newsletter_edicion.v3"
ESTADO_SCHEMA = "cl.sibylla.newsletter_state.v1"
TZ = ZoneInfo("America/Santiago")


def _hoy() -> str:
    return datetime.now(TZ).date().isoformat()


def _secciones_contexto(ctx: dict) -> list[tuple[str, str, list[dict]]]:
    out = [(g.get("id", ""), g.get("label", ""), g.get("cards") or [])
           for g in (ctx.get("grupos") or [])]
    tw = ctx.get("t") or {}
    out.extend([
        ("astronomia", tw.get("astro_heading", "Astronomía"), ctx.get("astro_cards") or []),
        ("divulgacion", tw.get("divulgacion_heading", "Divulgación"), ctx.get("divulgacion_cards") or []),
        ("sibylla", tw.get("sibylla_heading", "SIBYLLA"), ctx.get("sibylla_cards") or []),
    ])
    return [(sid, label, cards) for sid, label, cards in out
            if sid in (*TEMAS_VALIDOS, SECCION_FIJA) and cards]


def edicion_desde_contexto(ctx: dict, *, fecha: str | date | None = None) -> dict:
    """Crea la edición podada desde las mismas tarjetas de la portada."""
    if isinstance(fecha, date):
        fecha_s = fecha.isoformat()
    else:
        fecha_s = fecha or _hoy()
    secciones = []
    for sid, label, cards in _secciones_contexto(ctx):
        podadas = [{k: card.get(k) for k in CAMPOS_TARJETA} for card in cards]
        secciones.append({"id": sid, "label": label, "cards": podadas})
    return {
        "schema": EDICION_SCHEMA,
        "fecha": fecha_s,
        "generado": ctx.get("generado", ""),
        "total": int(ctx.get("total", 0) or 0),
        "n_fuentes": int(ctx.get("n_fuentes", 0) or 0),
        "site_url": ctx.get("site_url") or get_site_url(),
        "secciones": secciones,
    }


def escribir_edicion(edicion: dict, path: Path | str = EDICION_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(edicion, ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    return out


def cargar_edicion(path: Path | str = EDICION_PATH) -> dict | None:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if data.get("schema") != EDICION_SCHEMA or not isinstance(data.get("secciones"), list):
            return None
        return data
    except (OSError, ValueError, AttributeError):
        return None


def repartir(secciones: Iterable[dict], temas: Iterable[str], *,
             max_por_tema: int = MAX_POR_TEMA,
             max_total: int = MAX_TARJETAS) -> list[dict]:
    """Reparte tarjetas por rondas sin dejar el último tema en cero."""
    by_id = {s.get("id"): s for s in secciones if isinstance(s, dict)}
    orden: list[str] = []
    for tema in temas:
        if tema in TEMAS_VALIDOS and tema in by_id and tema not in orden:
            orden.append(tema)
    if SECCION_FIJA in by_id:
        orden.append(SECCION_FIJA)
    elegidas: dict[str, dict] = {
        sid: {"id": sid, "label": by_id[sid].get("label", sid), "cards": []}
        for sid in orden
    }
    for ronda in range(max(0, max_por_tema)):
        for sid in orden:
            if sum(len(s["cards"]) for s in elegidas.values()) >= max_total:
                break
            cards = by_id[sid].get("cards") or []
            limite = 2 if sid == SECCION_FIJA else max_por_tema
            if ronda < limite and ronda < len(cards):
                elegidas[sid]["cards"].append(cards[ronda])
    return [elegidas[sid] for sid in orden if elegidas[sid]["cards"]]


def cobertura_resumenes(secciones: Iterable[dict]) -> tuple[int, int]:
    """Devuelve (con resumen, elegibles) sin contar vídeos ni publicaciones propias."""
    elegibles = [
        card
        for seccion in secciones
        if seccion.get("id") != SECCION_FIJA
        for card in (seccion.get("cards") or [])
        if not card.get("is_video")
    ]
    return sum(bool(str(c.get("resumen") or "").strip()) for c in elegibles), len(elegibles)


def _dia_santiago(iso: str) -> date | None:
    """Día de calendario en Santiago de un instante ISO 8601. None si no parsea.

    La frescura se mide en días locales, no en horas, para que coincida con lo
    que el lector entiende por «hoy» y con `edicion["fecha"]`.
    """
    texto = str(iso or "").strip()
    if not texto:
        return None
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ).date()


def _dia_edicion(fecha: str) -> date:
    try:
        return date.fromisoformat(str(fecha))
    except ValueError:
        return datetime.now(TZ).date()


def _es_espanol(card: dict) -> bool:
    """¿La tarjeta está en español? (la marca la pone `web._tarjeta`).

    Compatibilidad: una edición sin el campo se trata como española, para que
    un despliegue a medias no vacíe el boletín.
    """
    valor = card.get("es_espanol")
    return True if valor is None else bool(valor)


def _breve_es_fresca(card: dict, seccion_id: str, dia: date, *,
                     max_dias: int = MAX_DIAS_BREVE) -> bool:
    """¿La breve entra en la ventana de antigüedad?

    Vídeos y publicaciones propias están exentos: son atemporales y filtrarlos
    dejaría sin correo a quien solo eligió Divulgación. Una tarjeta noticiosa
    sin fecha no puede demostrar que sea reciente, así que queda fuera.
    """
    if card.get("is_video") or seccion_id == SECCION_FIJA:
        return True
    publicada = _dia_santiago(card.get("published"))
    if publicada is None:
        return False
    return (dia - publicada).days <= max_dias


def _tier(card: dict) -> int:
    return {"I": 1, "II": 2, "III": 3}.get(str(card.get("seal_roman") or ""), 4)


def _candidatas(secciones: Iterable[dict], *, solo_espanol: bool = True) -> list[dict]:
    """Tarjetas que pueden ser destacada: con resumen, no vídeo, fuera de SIBYLLA.

    `solo_espanol=False` conserva las que no están en español; lo usa el
    diagnóstico del build para poder contar cuántas descarta el filtro.
    """
    candidatas: list[dict] = []
    for seccion in secciones:
        sid = str(seccion.get("id") or "")
        if sid == SECCION_FIJA:
            continue
        for posicion, card in enumerate(seccion.get("cards") or []):
            resumen = str(card.get("resumen") or "").strip()
            if not resumen or card.get("is_video"):
                continue
            if solo_espanol and not _es_espanol(card):
                continue
            candidatas.append({
                **dict(card),
                "section_id": sid,
                "section_label": seccion.get("label", sid),
                "position": posicion,
            })
    return candidatas


def _indice_rotacion(fecha: str, uid: str, total: int) -> int:
    if total <= 1:
        return 0
    semilla = f"{fecha}\0{uid}".encode("utf-8", errors="replace")
    return int.from_bytes(hashlib.sha256(semilla).digest()[:8], "big") % total


def _bandas_frescura(candidatas: list[dict], fecha: str) -> tuple[list[dict], list[dict]]:
    """Parte las candidatas en (publicadas el día de la edición, el día anterior).

    Las que no traen `published` no aparecen en ninguna banda: no se puede
    afirmar que sean del día.
    """
    dia = _dia_edicion(fecha)
    ayer_dia = dia - timedelta(days=1)
    hoy: list[dict] = []
    ayer: list[dict] = []
    for c in candidatas:
        publicada = _dia_santiago(c.get("published"))
        if publicada == dia:
            hoy.append(c)
        elif publicada == ayer_dia:
            ayer.append(c)
    return hoy, ayer


def diagnostico_candidatas(secciones: Iterable[dict], *, fecha: str | None = None) -> dict:
    """Conteos de la selección de destacada, para la línea de log del build.

    Mide tres cosas: si la banda «hoy» se queda vacía a menudo (el cron corre a
    media mañana de Santiago, así que cubre pocas horas de publicación), cuánto
    material pierde el filtro de idioma, y cuántas tarjetas llegan sin fecha.
    """
    fecha_s = fecha or _hoy()
    todas = _candidatas(secciones, solo_espanol=False)
    en_espanol = [c for c in todas if _es_espanol(c)]
    hoy, ayer = _bandas_frescura(en_espanol, fecha_s)
    return {
        "hoy": len(hoy),
        "ayer": len(ayer),
        "sin_fecha": sum(1 for c in en_espanol if _dia_santiago(c.get("published")) is None),
        "descartadas_idioma": len(todas) - len(en_espanol),
    }


def _elegir_destacada(secciones: Iterable[dict], *, fecha: str, uid: str) -> dict | None:
    """Elige la destacada: primero por frescura, luego por confianza/posición.

    La banda manda sobre el sello: una noticia de hoy con sello III gana a una
    de hace ocho días con sello I. La promesa del formato es «lo que está
    sucediendo», y sin este filtro la ventana ancha de Astronomía (30 días para
    fuentes chilenas) hacía que casi siempre ganara una nota de observatorio.
    Dentro de la banda elegida el orden es el de siempre: sello, posición en la
    sección y rotación determinista por (fecha, uid).
    """
    hoy, ayer = _bandas_frescura(_candidatas(secciones), fecha)
    candidatas = hoy or ayer
    if not candidatas:
        return None
    mejor_tier = min(_tier(c) for c in candidatas)
    candidatas = [c for c in candidatas if _tier(c) == mejor_tier]
    mejor_posicion = min(int(c["position"]) for c in candidatas)
    candidatas = [c for c in candidatas if int(c["position"]) == mejor_posicion]
    ids_seccion = sorted({str(c["section_id"]) for c in candidatas})
    sid = ids_seccion[_indice_rotacion(fecha, uid, len(ids_seccion))]
    return min((c for c in candidatas if c["section_id"] == sid),
               key=lambda c: str(c.get("id") or ""))


def _limitar_resumen(texto: str, limite: int = MAX_RESUMEN_DESTACADA) -> str:
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
    if len(limpio) <= limite:
        return limpio
    tramo = limpio[:limite + 1]
    finales = [tramo.rfind(marca) for marca in (". ", "! ", "? ")]
    corte = max(finales)
    if corte >= limite // 2:
        return tramo[:corte + 1].strip()
    corte_palabra = tramo.rfind(" ", 0, limite)
    return tramo[:corte_palabra if corte_palabra > 0 else limite].rstrip(" ,;:") + "…"


def _cards_breves(secciones: list[dict], destacada: dict, *,
                   fecha: str, max_breves: int = MAX_BREVES) -> list[dict]:
    """Reparte hasta `max_breves` señales, en español y dentro de la ventana.

    Si tras los filtros quedan menos de `max_breves`, el correo sale con las que
    haya: la destacada es la que sostiene la edición.
    """
    dia = _dia_edicion(fecha)

    def _apta(card: dict, seccion_id: str) -> bool:
        return _es_espanol(card) and _breve_es_fresca(card, seccion_id, dia)

    temas = [s for s in secciones if s.get("id") != SECCION_FIJA]
    fija = next((s for s in secciones if s.get("id") == SECCION_FIJA), None)
    sid_destacada = destacada.get("section_id")
    if temas and any(s.get("id") == sid_destacada for s in temas):
        indice = next(i for i, s in enumerate(temas) if s.get("id") == sid_destacada)
        temas = temas[indice + 1:] + temas[:indice + 1]

    breves: list[dict] = []
    id_destacada = destacada.get("id")
    rondas = max((len(s.get("cards") or []) for s in temas), default=0)
    if fija:
        rondas = max(rondas, len(fija.get("cards") or []))
    for ronda in range(rondas):
        for seccion in temas:
            cards = seccion.get("cards") or []
            if ronda >= len(cards) or cards[ronda].get("id") == id_destacada:
                continue
            if not _apta(cards[ronda], str(seccion.get("id") or "")):
                continue
            breves.append({**dict(cards[ronda]),
                            "section_id": seccion.get("id", ""),
                            "section_label": seccion.get("label", "")})
            if len(breves) >= max_breves:
                return breves
        if fija:
            cards_fijas = fija.get("cards") or []
            if (ronda < len(cards_fijas) and cards_fijas[ronda].get("id") != id_destacada
                    and _apta(cards_fijas[ronda], SECCION_FIJA)):
                breves.append({**dict(cards_fijas[ronda]),
                                "section_id": fija.get("id", ""),
                                "section_label": fija.get("label", "")})
                if len(breves) >= max_breves:
                    return breves
    return breves


def componer_boletin(secciones_personales: list[dict], secciones_globales: list[dict], *,
                     fecha: str, uid: str, max_breves: int = MAX_BREVES) -> dict | None:
    """Compone una destacada resumida y hasta cuatro señales personalizadas."""
    destacada = _elegir_destacada(secciones_personales, fecha=fecha, uid=uid)
    respaldo_editorial = destacada is None
    if destacada is None:
        destacada = _elegir_destacada(secciones_globales, fecha=fecha, uid=uid)
    if destacada is None:
        return None
    destacada = dict(destacada)
    destacada["resumen"] = _limitar_resumen(destacada.get("resumen") or "")
    return {
        "destacada": destacada,
        "breves": _cards_breves(secciones_personales, destacada,
                                fecha=fecha, max_breves=max_breves),
        "respaldo_editorial": respaldo_editorial,
    }


def _env_plantillas() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "sibylla" / "templates")),
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["wrap72"] = lambda value: textwrap.fill(
        str(value or ""), width=72, break_long_words=False, break_on_hyphens=False)
    return env


def _normalizar_card(card: dict, base: str) -> dict:
    copia = dict(card)
    url = str(copia.get("url") or "")
    if url and not url.startswith(("http://", "https://")):
        copia["url"] = f"{base}/{url.lstrip('/')}"
    elif not url:
        copia["url"] = f"{base}/#{copia.get('id', '')}"
    return copia


def _acortar(texto: str, limite: int) -> str:
    limpio = re.sub(r"\s+", " ", str(texto or "")).strip()
    if len(limpio) <= limite:
        return limpio
    corte = limpio.rfind(" ", 0, max(1, limite - 1))
    return limpio[:corte if corte > 0 else limite - 1].rstrip(" ,;:") + "…"


def construir_asunto(title: str, *, prueba: bool = False) -> str:
    """Construye un Subject informativo, acotado y sin posibilidad de CR/LF."""
    tr = load_translations("es")
    prefijo = t(tr, "newsletter.subject", title="")
    prefijo_prueba = t(tr, "newsletter.subject_test", subject="") if prueba else ""
    espacio = max(8, MAX_ASUNTO - len(prefijo_prueba) - len(prefijo))
    asunto = t(tr, "newsletter.subject", title=_acortar(title, espacio))
    if prueba:
        asunto = t(tr, "newsletter.subject_test", subject=asunto)
    return _acortar(asunto, MAX_ASUNTO)


def render_correo(edicion: dict, composicion: dict, *, site_url: str,
                  baja_url: str, baja_mailto: str) -> tuple[str, str]:
    tr = load_translations("es")
    base = site_url.rstrip("/")
    destacada = _normalizar_card(composicion["destacada"], base)
    breves = [_normalizar_card(c, base) for c in composicion.get("breves") or []]
    ctx = {
        "edicion": edicion,
        "destacada": destacada,
        "breves": breves,
        "respaldo_editorial": bool(composicion.get("respaldo_editorial")),
        "site_url": base,
        "baja_url": baja_url,
        "baja_mailto": baja_mailto,
        "t": tr["newsletter"],
        "footer_motto": tr["web"]["footer_motto"],
        "preheader": _acortar(destacada.get("resumen") or "", 140),
    }
    env = _env_plantillas()
    html = env.get_template("newsletter.html.j2").render(**ctx)
    texto = env.get_template("newsletter.txt.j2").render(**ctx)
    return html, texto


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    user: str
    password: str
    remitente: str
    modo: str = "ssl"
    throttle_s: float = 1.5


def smtp_config_desde_entorno() -> SmtpConfig | None:
    host = os.getenv("SMTP_HOST", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    remitente = os.getenv("SMTP_FROM", "").strip() or user
    try:
        port = int(os.getenv("SMTP_PORT", "465"))
        throttle = max(0.0, float(os.getenv("SMTP_THROTTLE_S", "1.5")))
    except ValueError:
        return None
    modo = os.getenv("SMTP_MODE", "starttls" if port == 587 else "ssl").strip().lower()
    if not host or not user or not password or "@" not in parseaddr(remitente)[1] or modo not in {"ssl", "starttls"}:
        return None
    return SmtpConfig(host, port, user, password, remitente, modo, throttle)


def construir_mensaje(destino: str, asunto: str, html: str, texto: str, *,
                      remitente: str, baja_url: str, baja_mailto: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = remitente
    msg["To"] = destino
    msg["Subject"] = asunto
    msg["Date"] = formatdate(localtime=False)
    dominio = parseaddr(remitente)[1].partition("@")[2] or "sibylla.cl"
    msg["Message-ID"] = make_msgid(domain=dominio)
    # RFC 2919: el identificador y su etiqueta se mantienen en ASCII para que
    # la librería no convierta List-Id en un encoded-word ambiguo.
    msg["List-Id"] = "Sibylla <boletin.sibylla.cl>"
    msg["List-Unsubscribe"] = f"<{baja_url}>, <{baja_mailto}>"
    msg["Precedence"] = "bulk"
    msg["X-Auto-Response-Suppress"] = "OOF, AutoReply"
    msg.set_content(texto)
    msg.add_alternative(html, subtype="html")
    return msg


def cargar_estado(path: Path | str = ESTADO_PATH) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return data if data.get("schema") == ESTADO_SCHEMA else {}
    except (OSError, ValueError, AttributeError):
        return {}


def guardar_estado(estado: dict, path: Path | str = ESTADO_PATH) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(estado, ensure_ascii=False, separators=(",", ":")) + "\n",
                   encoding="utf-8")
    tmp.replace(out)
    return out


def pendientes(suscriptores: Iterable[Suscriptor], estado: dict) -> list[Suscriptor]:
    """Devuelve quienes todavía no recibieron la edición del estado."""
    enviados = set(estado.get("enviados") or [])
    return [s for s in suscriptores if s.uid not in enviados]


def _enmascarar(email: str) -> str:
    local, sep, domain = email.partition("@")
    if not sep:
        return "***"
    host, dot, suffix = domain.partition(".")
    return f"{local[:1]}***@{host[:1]}***{dot}{suffix}"[:80]


def _estado_nuevo(hoy: str, total: int) -> dict:
    return {
        "schema": ESTADO_SCHEMA, "date": hoy, "terminado": False,
        "enviados": [], "fallidos": [], "omitidos": [], "total": total,
        "inicio": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "fin": None,
    }


class _CapturaRespuestaData:
    """Conserva la respuesta a DATA sin habilitar el debug inseguro de smtplib."""

    respuesta_data: tuple[int, str] | None = None

    def data(self, msg):  # noqa: ANN001 - firma heredada de smtplib
        codigo, respuesta = super().data(msg)
        if isinstance(respuesta, bytes):
            texto = respuesta.decode("utf-8", errors="replace")
        else:
            texto = str(respuesta)
        self.respuesta_data = (codigo, texto)
        return codigo, respuesta


class _SMTPConAcuse(_CapturaRespuestaData, smtplib.SMTP):
    pass


class _SMTPSSLConAcuse(_CapturaRespuestaData, smtplib.SMTP_SSL):
    pass


_QUEUE_ID_RE = re.compile(
    r"\b(?:queued\s+as|queue(?:\s+id)?|id)\b\s*[:=]?\s*<?([A-Za-z0-9][A-Za-z0-9._-]{2,79})",
    re.IGNORECASE,
)


def _acuse_data(conn) -> tuple[int | None, str | None]:
    """Extrae solo código y queue-id; nunca vuelca la respuesta SMTP completa."""
    respuesta = getattr(conn, "respuesta_data", None)
    if not respuesta:
        return None, None
    codigo, texto = respuesta
    match = _QUEUE_ID_RE.search(texto)
    return codigo, match.group(1) if match else None


def _log_aceptacion(conn, uid: str) -> None:
    codigo, queue_id = _acuse_data(conn)
    if codigo is None:
        log.warning("boletín: aceptado uid=%s; acuse DATA no disponible", uid[:6])
        return
    log.warning(
        "boletín: aceptado uid=%s smtp=%s cola=%s",
        uid[:6], codigo, queue_id or "no-informada",
    )


def _abrir_smtp(cfg: SmtpConfig):
    if cfg.modo == "starttls":
        conn = _SMTPConAcuse(cfg.host, cfg.port, timeout=30)
        conn.ehlo()
        conn.starttls(context=ssl.create_default_context())
        conn.ehlo()
    else:
        conn = _SMTPSSLConAcuse(cfg.host, cfg.port, timeout=30,
                               context=ssl.create_default_context())
    conn.login(cfg.user, cfg.password)
    return conn


def enviar(edicion: dict, suscriptores: Iterable[Suscriptor], *, estado: dict,
           cfg: SmtpConfig, site_url: str, tope: int,
           estado_path: Path | str = ESTADO_PATH, dry_run: bool = False,
           asunto_prueba: bool = False) -> dict:
    """Personaliza y envía, flusheando el estado tras cada destinatario."""
    tr = load_translations("es")
    baja_url = f"{site_url.rstrip('/')}/?boletin=baja"
    baja_mailto = ("mailto:baja@sibylla.cl?subject=" +
                   quote(tr["newsletter"]["baja_mailto_asunto"]))
    fecha = str(edicion["fecha"])
    todos = list(suscriptores)
    for clave in ("enviados", "fallidos", "omitidos"):
        estado.setdefault(clave, [])
    truncado = len(todos) > max(0, tope)
    lista = todos[:max(0, tope)]
    if truncado:
        log.warning("::warning::boletín: se alcanzó SIBYLLA_NEWSLETTER_MAX=%s", tope)
    conn = None
    reconexiones = 0
    fatal = False
    omitidos = 0
    try:
        for pos, suscriptor in enumerate(lista):
            secciones = repartir(edicion.get("secciones") or [], suscriptor.temas)
            composicion = componer_boletin(
                secciones,
                edicion.get("secciones") or [],
                fecha=fecha,
                uid=suscriptor.uid,
            )
            if composicion is None:
                omitidos += 1
                if dry_run:
                    log.info("boletín dry-run: uid=%s omitido; sin destacada fresca en español",
                             suscriptor.uid[:6])
                    continue
                if suscriptor.uid not in estado["omitidos"]:
                    estado["omitidos"].append(suscriptor.uid)
                guardar_estado(estado, estado_path)
                log.warning("boletín: omitido uid=%s; sin destacada fresca en español",
                            suscriptor.uid[:6])
                continue
            try:
                html, texto = render_correo(edicion, composicion, site_url=site_url,
                                            baja_url=baja_url, baja_mailto=baja_mailto)
                asunto = construir_asunto(
                    composicion["destacada"].get("title") or "",
                    prueba=asunto_prueba,
                )
                msg = construir_mensaje(suscriptor.email, asunto, html, texto,
                                        remitente=cfg.remitente, baja_url=baja_url,
                                        baja_mailto=baja_mailto)
                if dry_run:
                    log.info("boletín dry-run: uid=%s destino=%s tarjetas=%s bytes=%s",
                             suscriptor.uid[:6], _enmascarar(suscriptor.email),
                             1 + len(composicion["breves"]), len(msg.as_bytes()))
                    continue
                if conn is None:
                    conn = _abrir_smtp(cfg)
                conn.send_message(msg)
                _log_aceptacion(conn, suscriptor.uid)
            except smtplib.SMTPServerDisconnected:
                if reconexiones >= 2:
                    raise
                reconexiones += 1
                conn = _abrir_smtp(cfg)
                conn.send_message(msg)
                _log_aceptacion(conn, suscriptor.uid)
            except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
                    smtplib.SMTPDataError, UnicodeError, ValueError, OSError) as ex:
                estado["fallidos"].append({"uid": suscriptor.uid,
                                           "error": type(ex).__name__})
                guardar_estado(estado, estado_path)
                log.warning("boletín: fallo uid=%s destino=%s (%s)", suscriptor.uid[:6],
                            _enmascarar(suscriptor.email), type(ex).__name__)
                continue
            estado["enviados"].append(suscriptor.uid)
            guardar_estado(estado, estado_path)
            if cfg.throttle_s and pos < len(lista) - 1:
                time.sleep(cfg.throttle_s)
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPException, OSError) as ex:
        fatal = True
        log.warning("boletín: fallo fatal SMTP (%s)", type(ex).__name__)
    finally:
        if conn is not None:
            try:
                conn.quit()
            except (smtplib.SMTPException, OSError):
                pass
    if omitidos:
        # La ausencia de correo nunca debe ser silenciosa: si no hay destacada
        # del día ni del anterior, este aviso lo deja visible en el run.
        log.warning("::warning::boletín: %s de %s destinatarios sin edición fresca "
                    "(no hay destacada del día ni del anterior); no se les envió correo.",
                    omitidos, len(lista))
    if dry_run:
        return estado
    estado["terminado"] = not fatal and not truncado and not pendientes(todos, estado)
    estado["fin"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    guardar_estado(estado, estado_path)
    return estado


def enviar_boletin_cli() -> int:
    """Entrada CLI del paso posterior al deploy. Siempre devuelve 0."""
    try:
        load_env()
        edicion = cargar_edicion()
        hoy = _hoy()
        if not edicion or edicion.get("fecha") != hoy:
            log.warning("boletín: no hay una edición válida para hoy; no se envía")
            return 0
        test_to = os.getenv("SIBYLLA_NEWSLETTER_TEST_TO", "").strip()
        dry_run = os.getenv("SIBYLLA_NEWSLETTER_DRYRUN", "0").lower() in {"1", "true", "yes"}
        try:
            tope = max(0, int(os.getenv("SIBYLLA_NEWSLETTER_MAX", "80")))
        except ValueError:
            tope = 80
        cfg = smtp_config_desde_entorno()
        if cfg is None and not dry_run:
            log.warning("boletín: configuración SMTP incompleta; no se envía")
            return 0
        if cfg is None:  # dry-run: no se abre la conexión
            cfg = SmtpConfig("dry-run", 465, "dry-run", "dry-run",
                             "Sibylla <noticias@sibylla.cl>", throttle_s=0)
        if test_to:
            if "@" not in test_to:
                log.warning("boletín: SIBYLLA_NEWSLETTER_TEST_TO no es válido")
                return 0
            prueba = Suscriptor("prueba", test_to, TEMAS_VALIDOS)
            estado_prueba = _estado_nuevo(hoy, 1)
            # Estado temporal en memoria: el modo prueba nunca toca el estado diario.
            resultado_prueba = enviar(
                edicion, [prueba], estado=estado_prueba, cfg=cfg,
                site_url=edicion.get("site_url") or get_site_url(), tope=1,
                estado_path=ROOT / "data" / "newsletter_test_state.json",
                dry_run=dry_run, asunto_prueba=True,
            )
            if dry_run:
                log.warning("boletín prueba: dry-run completado; no se envió correo")
            elif resultado_prueba.get("enviados"):
                log.warning("boletín prueba: mensaje aceptado por el servidor SMTP")
            elif resultado_prueba.get("omitidos"):
                log.warning("boletín prueba: omitido porque la edición no tiene resumen elegible")
            else:
                errores = resultado_prueba.get("fallidos") or []
                causa = errores[0].get("error", "desconocida") if errores else "desconocida"
                log.warning("boletín prueba: no enviado (%s)", causa)
            try:
                (ROOT / "data" / "newsletter_test_state.json").unlink()
            except OSError:
                pass
            return 0
        estado = cargar_estado()
        from .suscriptores import fetch_suscriptores
        lectura = fetch_suscriptores()
        if not lectura.ok:
            log.warning(
                "boletín: lectura de Firestore fallida (%s); estado sin cambios",
                lectura.error or "desconocida",
            )
            return 0
        suscriptores = list(lectura.suscriptores)
        if estado.get("date") != hoy:
            estado = _estado_nuevo(hoy, len(suscriptores))
        else:
            estado["total"] = len(suscriptores)
        lista = pendientes(suscriptores, estado)
        enviados_antes = len(estado.get("enviados") or [])
        omitidos_antes = len(estado.get("omitidos") or [])
        fallidos_antes = len(estado.get("fallidos") or [])
        log.warning(
            "boletín: Firestore ok examinados=%s válidos=%s pendientes=%s enviados_previos=%s",
            lectura.examinados, len(suscriptores), len(lista), enviados_antes,
        )
        resultado = enviar(
            edicion, lista, estado=estado, cfg=cfg,
            site_url=edicion.get("site_url") or get_site_url(), tope=tope,
            dry_run=dry_run,
        )
        log.warning(
            "boletín: resumen aceptados=%s omitidos=%s fallidos=%s pendientes=%s "
            "terminado=%s dry_run=%s",
            len(resultado.get("enviados") or []) - enviados_antes,
            len(resultado.get("omitidos") or []) - omitidos_antes,
            len(resultado.get("fallidos") or []) - fallidos_antes,
            len(pendientes(suscriptores, resultado)),
            bool(resultado.get("terminado")), dry_run,
        )
    except Exception as ex:  # noqa: BLE001 - contrato: nunca rompe CI
        log.warning("boletín: fallo aislado en el comando de envío (%s)", ex)
    return 0
