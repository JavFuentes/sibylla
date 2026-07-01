"""Tests para la capa de red segura (sibylla.net).

Cubre la **lógica pura** de decisión anti-SSRF:
  - ``_ip_is_routable``   (¿IP pública/ruteable? con ``ipaddress``)
  - ``_is_http_url``      (solo http/https)
  - ``_host_is_safe``     (resuelve el host; con literales IP no hace red)

No se testa ``fetch_safe`` (necesita red: se haría con VCR/mocks más adelante,
en línea con la convención "sin red" de la suite). Los casos de ``_host_is_safe``
usan literales IP, que ``getaddrinfo`` resuelve **sin DNS** y por tanto sin red.
"""
import pytest

from sibylla.net import _host_is_safe, _ip_is_routable, _is_http_url, _redact_query, safe_error

# ---------------------------------------------------------------------------
# _ip_is_routable
# ---------------------------------------------------------------------------
ROUTABLE_CASES = [
    # (ip, esperado, descripción)
    ("8.8.8.8", True, "DNS público Google"),
    ("1.1.1.1", True, "DNS público Cloudflare"),
    ("203.0.113.10", False, "TEST-NET-3 (reservado para documentación)"),
    ("132.163.97.1", True, "IP pública NIST"),
    ("2606:4700:4700::1111", True, "IPv6 pública Cloudflare"),
    ("127.0.0.1", False, "loopback IPv4"),
    ("127.255.255.255", False, "extremo del loopback IPv4"),
    ("::1", False, "loopback IPv6"),
    ("10.0.0.5", False, "privada 10/8"),
    ("172.16.4.4", False, "privada 172.16/12"),
    ("192.168.1.1", False, "privada 192.168/16"),
    ("169.254.169.254", False, "metadata endpoint AWS/GCP (link-local)"),
    ("fe80::1", False, "link-local IPv6"),
    ("0.0.0.0", False, "sin especificar IPv4"),
    ("224.0.0.1", False, "multicast"),
    ("fc00::1", False, "ULA IPv6 (privada)"),
    ("no-es-una-ip", False, "cadena inválida"),
    ("", False, "cadena vacía"),
]


@pytest.mark.parametrize("ip, esperado, _desc", ROUTABLE_CASES)
def test_ip_is_routable(ip, esperado, _desc):
    assert _ip_is_routable(ip) is esperado


# ---------------------------------------------------------------------------
# _is_http_url
# ---------------------------------------------------------------------------
URL_SCHEME_CASES = [
    ("https://example.com/a", True, "https"),
    ("http://example.com", True, "http"),
    ("HTTPS://Example.com", True, "esquema en mayúsculas"),
    ("file:///etc/passwd", False, "file:// prohibido (acceso a disco local)"),
    ("gopher://example.com/x", False, "gopher:// prohibido (clásico SSRF)"),
    ("ftp://example.com/f", False, "ftp:// prohibido"),
    ("//example.com", False, "relativo sin esquema"),
    ("example.com", False, "sin esquema"),
    ("", False, "cadena vacía"),
]


@pytest.mark.parametrize("url, esperado, _desc", URL_SCHEME_CASES)
def test_is_http_url(url, esperado, _desc):
    assert _is_http_url(url) is esperado


# ---------------------------------------------------------------------------
# _host_is_safe (con literales IP: getaddrinfo no hace red)
# ---------------------------------------------------------------------------
HOST_CASES = [
    # (host, esperado, descripción)
    ("8.8.8.8", True, "IP pública literal"),
    ("1.1.1.1", True, "IP pública literal"),
    ("127.0.0.1", False, "loopback literal"),
    ("169.254.169.254", False, "metadata endpoint literal"),
    ("10.1.2.3", False, "privada 10/8 literal"),
    ("192.168.0.1", False, "privada 192.168/16 literal"),
    ("[::1]", False, "loopback IPv6 con corchetes"),
    ("[::1]:80", False, "loopback IPv6 con puerto"),
    ("8.8.8.8:443", True, "IP pública con puerto (se descarta el puerto)"),
    ("0.0.0.0", False, "sin especificar literal"),
]


@pytest.mark.parametrize("host, esperado, _desc", HOST_CASES)
def test_host_is_safe_ip_literals(host, esperado, _desc):
    assert _host_is_safe(host) is esperado


# ---------------------------------------------------------------------------
# _redact_query
# ---------------------------------------------------------------------------
REDACT_QUERY_CASES = [
    # (query, esperado, descripción)
    ("db=pubmed&api_key=SECRET&term=cancer", "db=pubmed&api_key=<redacted>&term=cancer", "api_key entre otros params"),
    ("key=ABC", "key=<redacted>", "key sola"),
    ("access_token=t&refresh_token=r", "access_token=<redacted>&refresh_token=<redacted>", "múltiples tokens"),
    ("foo=1&bar=2", "foo=1&bar=2", "sin secrets: intacto"),
    ("", "", "vacío"),
    ("code=1234", "code=<redacted>", "code (OAuth)"),
    ("id=5", "id=5", "id no es sensible"),
]


@pytest.mark.parametrize("qs, esperado, _desc", REDACT_QUERY_CASES)
def test_redact_query(qs, esperado, _desc):
    assert _redact_query(qs) == esperado


# ---------------------------------------------------------------------------
# safe_error
# ---------------------------------------------------------------------------
# (exc, debe_contener, no_debe_contener, descripción)
SAFE_ERROR_CASES = [
    (ValueError("bad parse"), "bad parse", "http", "sin URL: mensaje intacto"),
    (Exception(""), "Exception", "http", "mensaje vacío: cae al nombre del tipo"),
    (Exception("404 Client Error: Not Found for url: https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&api_key=SK_REAL_KEY_123"),
     "eutils.ncbi.nlm.nih.gov", "SK_REAL_KEY_123", "NCBI: api_key redactada, host preservado"),
    (Exception("HTTPConnectionPool(host='www.googleapis.com', port=443): Max retries exceeded with url: /youtube/v3/playlistItems?part=snippet&key=AIzaSY_FAKE_KEY (Caused by NewConnectionError)"),
     "playlistItems", "AIzaSY_FAKE_KEY", "YouTube: key redactada, path preservado"),
    (Exception("https://usuario:s3creto@host.example/api/x?token=tok"),
     "host.example", "s3creto", "userinfo user:pass redactado y token también"),
]


@pytest.mark.parametrize("exc, debe, no_debe, _desc", SAFE_ERROR_CASES)
def test_safe_error(exc, debe, no_debe, _desc):
    out = safe_error(exc)
    assert (debe in out) and (no_debe not in out)


def test_safe_error_trunca_mensajes_largos():
    out = safe_error(Exception("x" * 500))
    assert out.endswith("…") and len(out) <= 301
