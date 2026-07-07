"""Servidor admin local: herramienta del operador para administrar el sitio.

Primera función: ver y editar los canales de YouTube de la sección Divulgación.

- Escucha **solo** en ``127.0.0.1`` (puerto 8765 por defecto, ``--port``).
- Token anti-CSRF + validación de header ``Host`` (los endpoints escriben).
- Páginas server-side con formularios HTML clásicos (POST → redirect 303).
- **No commitea ni pushea**: solo edita archivos y avisa de cambios pendientes.
  Los cambios llegan a producción cuando el operador hace commit+push y el cron
  de CI regenera el sitio.

Filosofía del repo: stdlib puro (``http.server``), sin frameworks ni dependencias
nuevas. Las claves (YouTube API, credenciales SSH) se usan server-side y nunca
se imprimen en el HTML.
"""
from __future__ import annotations

import logging
import secrets
import subprocess
import tempfile
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .canales import alta_canal, baja_canal, estado_git, listar_canales
from .config import load_env
from .dashboard import (
    _jinja_env,
    _prepare_dashboard_context,
    fetch_runs_from_host,
)

log = logging.getLogger("sibylla")

DEFAULT_PORT = 8765

# Token CSRF del proceso (se (re)genera al arrancar ``serve``).
_CSRF_TOKEN: str = ""

# Path al runs.json descargado del host (best-effort; None si no se pudo).
_RUNS_PATH: Path | None = None


# --- Utilidades -------------------------------------------------------------


def _host_valido(host_header: str) -> bool:
    """True si el header Host es localhost/127.0.0.1 (anti DNS rebinding).

    El token CSRF es la protección principal; esta validación evita que un
    atacante que resuelve su dominio a 127.0.0.1 reache el endpoint.
    """
    if not host_header:
        return False
    # Quitar puerto si lo hay; normalizar.
    h = host_header.lower().split(":", 1)[0].strip()
    # Quitar corchetes de IPv6 loopback ([::1]).
    h = h.strip("[]")
    return h in ("localhost", "127.0.0.1", "::1")


def _refetch_runs() -> tuple[bool, str]:
    """Descarga runs.json del host a un path temporal. Devuelve (exito, msg).

    Best-effort: ante cualquier fallo (credenciales, scp, red), devuelve False
    y el llamador sigue. El path global ``_RUNS_PATH`` se actualiza solo si
    la descarga tiene éxito.
    """
    global _RUNS_PATH
    dest = Path(tempfile.gettempdir()) / "sibylla_admin_runs.json"
    try:
        fetch_runs_from_host(dest)
    except SystemExit as exc:
        return False, str(exc)
    except FileNotFoundError:
        return False, "No encuentro el comando 'scp'. Instala el cliente OpenSSH."
    except subprocess.CalledProcessError:
        return False, ("No se pudo descargar runs.json del host "
                       "(¿ya corrió el workflow y existe en el servidor?).")
    except Exception as exc:  # noqa: BLE001
        return False, f"Error descargando runs.json: {exc}"
    _RUNS_PATH = dest
    return True, "Historial de producción (runs.json) descargado del host."


def _leer_query_aviso(path: str) -> tuple[str, str]:
    """Extrae (aviso, aviso_tipo) del query string (post-redirect)."""
    q = parse_qs(urlparse(path).query)
    return q.get("aviso", [""])[0], q.get("aviso_tipo", [""])[0]


# --- Handler HTTP -----------------------------------------------------------


class _AdminHandler(BaseHTTPRequestHandler):
    server_version = "SibyllaAdmin/1.0"

    # Silenciar el log ruidoso por defecto; enrutar al logger de sibylla.
    def log_message(self, format: str, *args) -> None:
        log.info("admin %s - %s", self.address_string(), format % args)

    # --- GET ---
    def do_GET(self):  # noqa: N802
        try:
            self._router_get()
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en GET %s", self.path)
            self._send_text(500, f"Error interno: {exc}")

    def _router_get(self):
        path = urlparse(self.path).path
        if path in ("/", ""):
            return self._redirect("/metricas")
        if path == "/metricas":
            return self._serve_metricas()
        if path == "/divulgacion":
            return self._serve_divulgacion()
        return self._send_text(404, "Página no encontrada")

    # --- POST ---
    def do_POST(self):  # noqa: N802
        try:
            self._router_post()
        except Exception as exc:  # noqa: BLE001
            log.exception("Error en POST %s", self.path)
            self._send_text(500, f"Error interno: {exc}")

    def _router_post(self):
        if not _host_valido(self.headers.get("Host", "")):
            return self._send_text(403, "Host no permitido")
        path = urlparse(self.path).path
        if path == "/api/canales/agregar":
            return self._post_agregar()
        if path == "/api/canales/quitar":
            return self._post_quitar()
        if path == "/api/metricas/actualizar":
            return self._post_metricas_actualizar()
        return self._send_text(404, "Endpoint no encontrado")

    # --- Páginas ---

    def _serve_metricas(self):
        aviso, aviso_tipo = _leer_query_aviso(self.path)
        try:
            ctx = _prepare_dashboard_context(_RUNS_PATH)
        except Exception as exc:  # noqa: BLE001
            log.warning("No se pudo preparar el contexto del dashboard: %s", exc)
            # Caer a un contexto vacío (mensaje "sin datos") en lugar de romper.
            ctx = _prepare_dashboard_context(None)
        ctx["activa"] = "metricas"
        ctx["git_dirty"] = estado_git()
        ctx["csrf"] = _CSRF_TOKEN
        if aviso:
            ctx["aviso"] = aviso
            ctx["aviso_tipo"] = aviso_tipo or "ok"
        html = _jinja_env().get_template("dashboard.html.j2").render(**ctx)
        self._send_html(html)

    def _serve_divulgacion(self):
        aviso, aviso_tipo = _leer_query_aviso(self.path)
        canales_lista = listar_canales()
        html = _jinja_env().get_template("divulgacion.html.j2").render(
            activa="divulgacion",
            git_dirty=estado_git(),
            csrf=_CSRF_TOKEN,
            canales=canales_lista,
            total=len(canales_lista),
            aviso=aviso,
            aviso_tipo=aviso_tipo or "ok",
        )
        self._send_html(html)

    # --- POST handlers ---

    def _post_agregar(self):
        form = self._read_form()
        if form.get("csrf") != _CSRF_TOKEN:
            return self._send_text(403, "Token CSRF inválido")
        entrada = form.get("entrada", "").strip()
        nombre = form.get("nombre", "").strip()
        ok, msg = alta_canal(entrada, nombre or None)
        self._redirect("/divulgacion",
                       f"aviso={quote(msg)}&aviso_tipo={'ok' if ok else 'error'}")

    def _post_quitar(self):
        form = self._read_form()
        if form.get("csrf") != _CSRF_TOKEN:
            return self._send_text(403, "Token CSRF inválido")
        canal_id = form.get("canal_id", "").strip()
        ok, msg = baja_canal(canal_id)
        self._redirect("/divulgacion",
                       f"aviso={quote(msg)}&aviso_tipo={'ok' if ok else 'error'}")

    def _post_metricas_actualizar(self):
        form = self._read_form()
        if form.get("csrf") != _CSRF_TOKEN:
            return self._send_text(403, "Token CSRF inválido")
        ok, msg = _refetch_runs()
        self._redirect("/metricas",
                       f"aviso={quote(msg)}&aviso_tipo={'ok' if ok else 'error'}")

    # --- Helpers HTTP ---

    def _read_form(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length > 0 else b""
        parsed = parse_qs(raw.decode("utf-8", errors="replace"), keep_blank_values=True)
        return {k: v[0] for k, v in parsed.items()}

    def _send_html(self, html: str, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, status: int, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str, query: str | None = None) -> None:
        target = location + (f"?{query}" if query else "")
        self.send_response(HTTPStatus.SEE_OTHER)  # 303 → GET
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()


# --- Punto de entrada -------------------------------------------------------


def serve(port: int = DEFAULT_PORT) -> int:
    """Arranca el servidor admin en 127.0.0.1:<port> y abre el navegador.

    Bloquea (``serve_forever``) hasta que el operador pulse Ctrl+C. Devuelve 0.
    """
    global _CSRF_TOKEN
    load_env()
    _CSRF_TOKEN = secrets.token_hex(32)

    # Best-effort: descargar runs.json del host. Si falla, /metricas muestra
    # aviso pero la herramienta (/divulgacion) sigue funcionando.
    print("→ Descargando historial de producción (runs.json) del host…")
    ok, msg = _refetch_runs()
    if ok:
        print(f"✓ {msg}")
    else:
        print(f"⚠ {msg} — /metricas mostrará el historial local (la "
              "administración sigue funcionando).")

    httpd = ThreadingHTTPServer(("127.0.0.1", port), _AdminHandler)
    url = f"http://127.0.0.1:{port}/metricas"
    print(f"\n✓ Servidor admin de Sibylla escuchando en {url}")
    print("  Cierra con Ctrl+C.\n")
    try:
        webbrowser.open(url)
    except Exception:  # noqa: BLE001
        # El navegador no es crítico: el operador puede abrir la URL a mano.
        pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n■ Servidor detenido.")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(serve())
