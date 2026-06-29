"""Tests para _repair_link (saneamiento de <link> malformados en feeds RSS/Atom).

Cubre el caso real del feed de Interferencia (Drupal), que mete el ancla HTML
del título URL-encodeada dentro del <link>, produciendo enlaces a páginas vacías.
"""
import pytest

from sibylla.fetchers import _repair_link

BASE = "https://interferencia.cl/rss.xml"

REPAIR_CASES = [
    # (entrada, base, esperado, descripción)
    (
        "https://interferencia.cl/%3Ca%20href%3D%22/articulos/la-factura-geopolitica-de-haber-subestimado-america-latina%22%3ELa%20factura%3C/a%3E",
        BASE,
        "https://interferencia.cl/articulos/la-factura-geopolitica-de-haber-subestimado-america-latina",
        "link Drupal de Interferencia: recupera el slug real desde el ancla embebida",
    ),
    (
        "https://www.ciperchile.cl/2026/06/28/titulo-de-la-noticia/",
        BASE,
        "https://www.ciperchile.cl/2026/06/28/titulo-de-la-noticia/",
        "link limpio sin HTML: se devuelve intacto",
    ),
    (
        "",
        BASE,
        "",
        "link vacío -> vacío",
    ),
    (
        "https://interferencia.cl/%3Cdiv%3Esin%20href%3C/div%3E",
        BASE,
        "",
        "HTML embebido sin <a href>: irrecuperable -> vacío (descartar)",
    ),
    (
        'https://interferencia.cl/%3Ca%20href%3D%22https://otro.cl/x%22%3Ex%3C/a%3E',
        BASE,
        "https://otro.cl/x",
        "href absoluto dentro del ancla: se respetan el host del href",
    ),
    (
        "https://x%3Ca href='/articulos/rel'%3Ey%3C/a%3E",
        BASE,
        "https://interferencia.cl/articulos/rel",
        "href con comillas simples: también se parsea",
    ),
    (
        "https://interferencia.cl/%3Ca%20href%3D%22/articulos/slug%22%3E",
        "",
        "",
        "href site-absoluto sin base_url: irrecuperable -> vacío",
    ),
]


@pytest.mark.parametrize("link,base,expected,_desc", REPAIR_CASES)
def test_repair_link(link, base, expected, _desc):
    assert _repair_link(link, base) == expected
