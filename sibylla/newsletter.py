"""Construcción y envío del boletín diario de Sibylla.

El build escribe una edición global. El paso posterior al deploy la reparte
según los temas de cada suscriptor y persiste el avance por uid tras cada
destinatario. Todos los fallos son aislados: este módulo nunca debe poner en
riesgo la publicación del sitio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
import json
import logging
import os
from pathlib import Path
import smtplib
import ssl
import textwrap
import time
from typing import Any, Iterable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .config import ROOT, get_site_url, load_env
from .i18n import load_translations, t
from .llm import get_provider
from .suscriptores import Suscriptor

log = logging.getLogger("sibylla")

# Debe coincidir con TEMAS_VALIDOS de suscriptores.py, temasBoletin() de
# firestore.rules y TEMAS_BOLETIN de static/social.js.
TEMAS_VALIDOS = ("nacional", "ai", "medicine", "astronomia", "divulgacion")
SECCION_FIJA = "sibylla"
MAX_POR_TEMA = 3
MAX_TARJETAS = 12
CAMPOS_TARJETA = (
    "id", "url", "title", "source_name", "date", "snippet", "resumen",
    "seal_roman", "is_video",
)
EDICION_PATH = ROOT / "data" / "newsletter_edicion.json"
ESTADO_PATH = ROOT / "data" / "newsletter_state.json"
EDICION_SCHEMA = "cl.sibylla.newsletter_edicion.v1"
ESTADO_SCHEMA = "cl.sibylla.newsletter_state.v1"
TZ = ZoneInfo("America/Santiago")
MESES_ES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
            "agosto", "septiembre", "octubre", "noviembre", "diciembre")


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


def edicion_desde_contexto(ctx: dict, *, sintesis: str, fecha: str | date | None = None) -> dict:
    """Crea la edición podada desde las mismas tarjetas de la portada."""
    if isinstance(fecha, date):
        fecha_s = fecha.isoformat()
    else:
        fecha_s = fecha or _hoy()
    secciones = []
    for sid, label, cards in _secciones_contexto(ctx):
        podadas = [{k: card.get(k) for k in CAMPOS_TARJETA} for card in cards]
        secciones.append({"id": sid, "label": label, "cards": podadas})
    fallback = t(load_translations("es"), "newsletter.sintesis_fallback",
                 count=ctx.get("total", 0), sources=ctx.get("n_fuentes", 0))
    sintesis_limpia = (sintesis or fallback).strip()
    return {
        "schema": EDICION_SCHEMA,
        "fecha": fecha_s,
        "generado": ctx.get("generado", ""),
        "sintesis": sintesis_limpia,
        "sintesis_llm": sintesis_limpia != fallback,
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


def construir_sintesis(ctx: dict, *, tracker: list[dict] | None = None) -> str:
    """Genera una síntesis global; ante cualquier fallo usa texto determinista."""
    tr = load_translations("es")
    fallback = t(tr, "newsletter.sintesis_fallback",
                 count=ctx.get("total", 0), sources=ctx.get("n_fuentes", 0))
    filas: list[str] = []
    for _sid, label, cards in _secciones_contexto(ctx):
        for card in cards:
            if len(filas) >= 30:
                break
            filas.append(f"{label}: {card.get('title', '')}")
    try:
        provider = get_provider()
        if provider is None:
            return fallback
        system = t(tr, "newsletter.system_prompt")
        user = t(tr, "newsletter.user_prompt", items_json="\n".join(filas))
        resp = provider.complete(system, user, max_tokens=400, temperature=0.5)
        texto = (resp.text or "").strip()
        if not texto:
            return fallback
        if tracker is not None:
            usage = resp.usage or {}
            tracker.append({
                "purpose": "newsletter_sintesis",
                "model": f"{provider.name}:{provider.model}",
                "input": usage.get("input", 0),
                "output": usage.get("output", 0),
            })
        return texto
    except Exception as ex:  # noqa: BLE001 - degradación obligatoria a fallback
        log.warning("boletín: síntesis LLM no disponible (%s); uso fallback", ex)
        return fallback


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


def _env_plantillas() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(ROOT / "sibylla" / "templates")),
        autoescape=select_autoescape(enabled_extensions=("html.j2",), default_for_string=False),
        trim_blocks=True, lstrip_blocks=True,
    )
    env.filters["wrap72"] = lambda value: textwrap.fill(
        str(value or ""), width=72, break_long_words=False, break_on_hyphens=False)
    return env


def render_correo(edicion: dict, secciones: list[dict], *, site_url: str,
                  baja_url: str, baja_mailto: str) -> tuple[str, str]:
    tr = load_translations("es")
    base = site_url.rstrip("/")
    normalizadas: list[dict] = []
    for seccion in secciones:
        copia = {**seccion, "cards": []}
        for card in seccion.get("cards") or []:
            c = dict(card)
            url = str(c.get("url") or "")
            if url and not url.startswith(("http://", "https://")):
                c["url"] = f"{base}/{url.lstrip('/')}"
            elif not url:
                c["url"] = f"{base}/#{c.get('id', '')}"
            copia["cards"].append(c)
        normalizadas.append(copia)
    ctx = {
        "edicion": edicion,
        "secciones": normalizadas,
        "site_url": base,
        "baja_url": baja_url,
        "baja_mailto": baja_mailto,
        "t": tr["newsletter"],
        "footer_motto": tr["web"]["footer_motto"],
        "preheader": (edicion.get("sintesis") or "")[:90],
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
    msg["List-Id"] = "Boletín de Sibylla <boletin.sibylla.cl>"
    msg["List-Unsubscribe"] = f"<{baja_url}>, <{baja_mailto}>"
    msg["Precedence"] = "bulk"
    msg["Auto-Submitted"] = "auto-generated"
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


def debe_enviar(estado: dict, hoy: str | date) -> bool:
    hoy_s = hoy.isoformat() if isinstance(hoy, date) else hoy
    return estado.get("date") != hoy_s or not estado.get("terminado", False)


def pendientes(suscriptores: Iterable[Suscriptor], estado: dict) -> list[Suscriptor]:
    resueltos = set(estado.get("enviados") or []) | set(estado.get("omitidos") or [])
    return [s for s in suscriptores if s.uid not in resueltos]


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


def _abrir_smtp(cfg: SmtpConfig):
    if cfg.modo == "starttls":
        conn = smtplib.SMTP(cfg.host, cfg.port, timeout=30)
        conn.ehlo()
        conn.starttls(context=ssl.create_default_context())
        conn.ehlo()
    else:
        conn = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30,
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
    fecha = datetime.fromisoformat(edicion["fecha"]).date()
    asunto = t(tr, "newsletter.subject", day=fecha.day, month=MESES_ES[fecha.month - 1])
    if asunto_prueba:
        asunto = t(tr, "newsletter.subject_test", subject=asunto)
    todos = list(suscriptores)
    truncado = len(todos) > max(0, tope)
    lista = todos[:max(0, tope)]
    if truncado:
        log.warning("::warning::boletín: se alcanzó SIBYLLA_NEWSLETTER_MAX=%s", tope)
    conn = None
    reconexiones = 0
    fatal = False
    try:
        if not dry_run and lista:
            conn = _abrir_smtp(cfg)
        for pos, suscriptor in enumerate(lista):
            secciones = repartir(edicion.get("secciones") or [], suscriptor.temas)
            if not any(s["cards"] for s in secciones):
                if suscriptor.uid not in estado["omitidos"]:
                    estado["omitidos"].append(suscriptor.uid)
                guardar_estado(estado, estado_path)
                continue
            try:
                html, texto = render_correo(edicion, secciones, site_url=site_url,
                                            baja_url=baja_url, baja_mailto=baja_mailto)
                msg = construir_mensaje(suscriptor.email, asunto, html, texto,
                                        remitente=cfg.remitente, baja_url=baja_url,
                                        baja_mailto=baja_mailto)
                if dry_run:
                    log.info("boletín dry-run: uid=%s destino=%s tarjetas=%s bytes=%s",
                             suscriptor.uid[:6], _enmascarar(suscriptor.email),
                             sum(len(s["cards"]) for s in secciones), len(msg.as_bytes()))
                    continue
                assert conn is not None
                conn.send_message(msg)
            except smtplib.SMTPServerDisconnected:
                if reconexiones >= 2:
                    raise
                reconexiones += 1
                conn = _abrir_smtp(cfg)
                conn.send_message(msg)
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
    if dry_run:
        return estado
    estado["terminado"] = not fatal and not truncado
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
            enviar(edicion, [prueba], estado=estado_prueba, cfg=cfg,
                   site_url=edicion.get("site_url") or get_site_url(), tope=1,
                   estado_path=ROOT / "data" / "newsletter_test_state.json",
                   dry_run=dry_run, asunto_prueba=True)
            try:
                (ROOT / "data" / "newsletter_test_state.json").unlink()
            except OSError:
                pass
            return 0
        estado = cargar_estado()
        if not debe_enviar(estado, hoy):
            log.info("boletín: la edición de hoy ya terminó")
            return 0
        from .suscriptores import fetch_suscriptores
        suscriptores = fetch_suscriptores()
        if estado.get("date") != hoy:
            estado = _estado_nuevo(hoy, len(suscriptores))
        lista = pendientes(suscriptores, estado)
        enviar(edicion, lista, estado=estado, cfg=cfg,
               site_url=edicion.get("site_url") or get_site_url(), tope=tope,
               dry_run=dry_run)
    except Exception as ex:  # noqa: BLE001 - contrato: nunca rompe CI
        log.warning("boletín: fallo aislado en el comando de envío (%s)", ex)
    return 0
