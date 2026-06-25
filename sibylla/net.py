"""Red segura: utilidades para fetchear URLs de origen no confiable sin SSRF.

Los fetchers de feeds (arXiv, PubMed, Google News, medios por RSS...) traen
URLs que **no controlamos**: una fuente comprometida o maliciosa puede apuntar
a direcciones internas (metadata de la nube, loopback, red privada). Esta
capa filtra esos destinos antes de descargar nada.

Diseño:
- ``_ip_is_routable`` es **lógica pura** (``ipaddress``) → testeable sin red.
- ``_host_is_safe`` resuelve el host a sus IPs (``getaddrinfo``) y exige que
  *todas* sean ruteables. Resuelve también literales IP sin DNS.
- ``fetch_safe`` descarga siguiendo redirects **a mano**, re-validando el
  host en cada salto (un redirect a 169.254.169.254 se bloquea aquí), e
  impone un **tope de bytes** para evitar descargas enormes (DoS/memoria).

Solo la usa ``articles.py`` (contenido de prensa de URLs arbitrarias). Los
fetchers que consultan endpoints fijos y de confianza (arXiv, PubMed...)
siguen usando ``_get`` directo: ahí el host es conocido y no hay riesgo.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from typing import Optional
from urllib.parse import urljoin, urlsplit

import requests

log = logging.getLogger("sibylla")

# Tope blando al descargar: 2 MB basta para el cuerpo de un artículo de prensa
# (trafilatura solo necesita el texto). Evita que un feed malicioso sirva un
# HTML gigante y sature memoria/disco en CI.
DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_TIMEOUT = 20
DEFAULT_MAX_REDIRECTS = 5

# Navegador inocuo: no nos identificamos como bot de un scraper agresivo.
UA = "Sibylla/0.1 (news research aggregator; +https://sibylla.cl)"


class UnsafeURL(Exception):
    """URL que apunta a un destino no ruteable (SSRF bloqueado)."""


def _ip_is_routable(ip: str) -> bool:
    """True si la IP es pública/ruteable (no privada, loopback, link-local...).

    Cubre los rangos peligrosos para SSRF: privados (10/8, 172.16/12,
    192.168/16, fc00::/7), loopback (127/8, ::1), link-local (169.254/16 →
    incluye el *metadata endpoint* 169.254.169.254 de AWS/GCP), reservados,
    multicast y sin especificar. Lógica pura (sin red).
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def _is_http_url(url: str) -> bool:
    """True solo para esquemas http/https (nada de file://, gopher://, ...)."""
    if not url:
        return False
    return urlsplit(url).scheme.lower() in ("http", "https")


def _host_is_safe(host: str) -> bool:
    """True si el host resuelve **solo** a IPs ruteables.

    Resuelve el host (DNS o literal IP) y exige que **todas** las IPs devueltas
    sean ruteables: si aparece una sola IP interna, se rechaza (evita DNS con
    resultados mixtos o rebindings hacia direcciones privadas). Si la
    resolución falla, se considera inseguro (fail-closed).
    """
    if not host:
        return False
    # Quita el puerto y corchetes de literales IPv6 ("[::1]:80" -> "::1").
    raw = host.split("@")[-1]
    if raw.startswith("[") and "]" in raw:
        raw = raw[1:raw.index("]")]
    else:
        raw = raw.rsplit(":", 1)[0] if raw.count(":") == 1 else raw
    try:
        infos = socket.getaddrinfo(raw, None)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for _, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        # Normaliza IPv6 con zona ("fe80::1%eth0") quitando la zona.
        if "%" in ip:
            ip = ip.split("%", 1)[0]
        if not _ip_is_routable(ip):
            return False
    return True


def _url_host_is_safe(url: str) -> bool:
    parts = urlsplit(url)
    return bool(parts.netloc) and _host_is_safe(parts.netloc)


def fetch_safe(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: int = DEFAULT_TIMEOUT,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
    headers: Optional[dict] = None,
) -> bytes:
    """Descarga ``url`` a bytes con guarda anti-SSRF y tope de tamaño.

    - Solo http/https.
    - Valida el host (sin IPs internas) antes de cada petición, incluidos los
      saltos de redirección (se siguen a mano, re-validando cada Location).
    - Lee el cuerpo hasta ``max_bytes``; si el servidor envía más, se trunca
      (no se eleva: alcanza para trafilatura y evita OOM).
    Lanza ``UnsafeURL`` si el destino no es ruteable y ``requests`` si falla
    el transporte/HTTP. El llamador debe envolver en try/except y degradar.
    """
    if not _is_http_url(url):
        raise UnsafeURL(f"Esquema no permitido (solo http/https): {url!r}")
    if not _url_host_is_safe(url):
        raise UnsafeURL(f"Host no ruteable (SSRF bloqueado): {url!r}")

    merged = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"}
    if headers:
        merged.update(headers)

    current = url
    for _ in range(max_redirects + 1):
        resp = requests.get(
            current, allow_redirects=False, stream=True,
            timeout=timeout, headers=merged,
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location", "")
            if not location:
                break
            current = urljoin(current, location)
            if not _is_http_url(current):
                raise UnsafeURL(f"Redirect a esquema no permitido: {current!r}")
            if not _url_host_is_safe(current):
                raise UnsafeURL(f"Redirect a host no ruteable (SSRF): {current!r}")
            resp.close()
            continue
        resp.raise_for_status()
        return _read_capped(resp, max_bytes)
    raise UnsafeURL(f"Demasiados redirects (> {max_redirects}) desde {url!r}")


def _read_capped(resp: requests.Response, max_bytes: int) -> bytes:
    """Lee el cuerpo hasta ``max_bytes`` bytes; trunca si el servidor envía más."""
    chunks = []
    total = 0
    try:
        for chunk in resp.iter_content(chunk_size=8192):
            if not chunk:
                continue
            remaining = max_bytes - total
            if remaining <= 0:
                break
            if len(chunk) > remaining:
                chunks.append(chunk[:remaining])
                total = max_bytes
                break
            chunks.append(chunk)
            total += len(chunk)
    finally:
        resp.close()
    return b"".join(chunks)
