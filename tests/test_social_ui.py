"""Contratos estáticos de la interacción social de las tarjetas."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = (ROOT / "sibylla" / "templates" / "index.html.j2").read_text(
    encoding="utf-8"
)
SOCIAL_JS = (ROOT / "static" / "social.js").read_text(encoding="utf-8")


def test_video_embebido_marca_la_tarjeta_antes_de_reemplazar_la_miniatura():
    """Ver un video localmente debe contar como lectura aunque Firebase tarde."""
    marca = "carta.dataset.contenidoVisto = 'true';"
    reemplazo = "a.innerHTML = '';"

    assert marca in TEMPLATE
    assert "sibylla:contenido-visto" in TEMPLATE
    assert TEMPLATE.index(marca) < TEMPLATE.index(reemplazo)


def test_social_reconoce_video_visto_antes_de_cargar():
    """El estado DOM cubre el clic ocurrido antes de inicializar social.js."""
    assert "carta.dataset.contenidoVisto === 'true'" in SOCIAL_JS
    assert "document.addEventListener('sibylla:contenido-visto'" in SOCIAL_JS


def test_contadores_sociales_permanecen_en_flujo_con_separacion():
    """Los conteos no deben volver a quedar pegados bajo los iconos."""
    assert ".soc-btn{ position:relative; width:auto;" in TEMPLATE
    assert "justify-content:center; gap:8px;" in TEMPLATE
    assert ".soc-num{ min-width:1ch;" in TEMPLATE
    assert "bottom:-12px" not in TEMPLATE
