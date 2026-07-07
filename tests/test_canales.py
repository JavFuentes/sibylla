"""Tests de sibylla.canales (lógica de gestión de canales de YouTube).

Cubre las funciones puras (sobre strings) y valida alta+baja como inversas
escribiendo a tmp_path y re-parseando con load_registry. Sin red: las
funciones que hacen HTTP (resolver_channel_id, verificar_canal) no se testean
aquí (convención del repo: requests mockeado o sin red).
"""
import pytest
import yaml

from sibylla import canales
from sibylla.canales import (
    agregar_a_pipeline,
    agregar_canal_yaml,
    generar_id,
    parsear_entrada,
    quitar_canal_yaml,
    quitar_de_pipeline,
)
from sibylla.config import load_registry


# --- Fixtures (replican la estructura real, versión mínima) -----------------

YAML_FIXTURE = """# sources.yaml de prueba
meta:
  version: 1

sources:
  - id: arxiv_api
    name: "arXiv"
    tier: 1
  - id: google_news_rss
    name: "Google News"
    tier: 2

# =============================================================================
# DIVULGACIÓN — canales de YouTube
# =============================================================================
  - id: yt_alpha
    name: "Alpha"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    handle: "@alpha"
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-07-01

  - id: yt_beta
    name: "Beta"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    handle: "@beta"
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCbbbbbbbbbbbbbbbbbbbbbb"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-07-01
    notes: "Feed Atom flaky."

# =============================================================================
# Voces de la red
# =============================================================================
social:
  shuffle: true
"""

PIPELINE_FIXTURE = '''"""pipeline.py de prueba"""
DEFAULT_FREE_SOURCES = [
    # APIs
    "arxiv_api", "google_news_rss",
    # Divulgación
    "yt_alpha", "yt_beta",
    # Social
    "mastodon", "bluesky",
]
'''


def _datos_canal(cid: str = "UCcccccccccccccccccccccc", id_="yt_nuevo",
                 name="Nuevo Canal", handle="@nuevo") -> dict:
    return {
        "id": id_,
        "name": name,
        "handle": handle,
        "channel_id": cid,
        "url": f"https://www.youtube.com/feeds/videos.xml?channel_id={cid}",
        "status": "verified_2026-07-07",
    }


# --- parsear_entrada --------------------------------------------------------

PARSEAR_CASES = [
    ("UCbbbbbbbbbbbbbbbbbbbbbb", "channel_id", "UCbbbbbbbbbbbbbbbbbbbbbb", "UC pelado"),
    ("  UCbbbbbbbbbbbbbbbbbbbbbb  ", "channel_id", "UCbbbbbbbbbbbbbbbbbbbbbb", "espacios extremos"),
    ("https://www.youtube.com/channel/UCz6Cf-gSFs6jDdCo5vctKHA",
     "channel_id", "UCz6Cf-gSFs6jDdCo5vctKHA", "URL /channel/UC..."),
    ("https://www.youtube.com/@midudev", "handle", "@midudev", "URL /@handle"),
    ("http://youtube.com/@Alpha.Beta-99", "handle", "@Alpha.Beta-99", "URL con puntos y guion"),
    ("@midudev", "handle", "@midudev", "@handle suelto"),
    ("midudev", "handle", "@midudev", "handle pelado -> añade @"),
    ("@alpha_beta", "handle", "@alpha_beta", "handle con underscore"),
    ("https://www.youtube.com/user/legacyuser", "handle", "@legacyuser", "URL /user/ legacy"),
    ("https://www.youtube.com/c/NamedChannel", "handle", "@NamedChannel", "URL /c/ legacy"),
]


@pytest.mark.parametrize("entrada,tipo,valor,_desc", PARSEAR_CASES)
def test_parsear_entrada(entrada, tipo, valor, _desc):
    res = parsear_entrada(entrada)
    assert res == {"tipo": tipo, "valor": valor}


def test_parsear_entrada_vacia_lanza():
    with pytest.raises(ValueError):
        parsear_entrada("")


def test_parsear_entrada_url_desconocida_lanza():
    with pytest.raises(ValueError):
        parsear_entrada("https://example.com/foo")


# --- generar_id -------------------------------------------------------------

GENERAR_ID_CASES = [
    ("@midudev", "yt_midudev", "handle simple"),
    ("midudev", "yt_midudev", "sin @"),
    ("@MiduDev", "yt_midudev", "mayúsculas -> minúsculas"),
    ("@Canal con espacios", "yt_canal_con_espacios", "espacios -> underscore"),
    ("@Veritasium en Español", "yt_veritasium_en_espanol", "ñ y tildes -> ASCII-fold"),
    ("@QuantumFracture", "yt_quantumfracture", "camelCase"),
    ("@C++ & Python!!!", "yt_c_python", "símbolos colapsados a _"),
]


@pytest.mark.parametrize("handle,esperado,_desc", GENERAR_ID_CASES)
def test_generar_id(handle, esperado, _desc):
    assert generar_id(handle, set()) == esperado


def test_generar_id_colision_lanza():
    with pytest.raises(ValueError):
        generar_id("@midudev", {"yt_midudev"})


def test_generar_id_solo_simbolos_lanza():
    with pytest.raises(ValueError):
        generar_id("@!!!", set())


# --- Cirugía YAML -----------------------------------------------------------

def test_agregar_canal_yaml_inserta_bloque_y_conserva_social():
    resultado = agregar_canal_yaml(YAML_FIXTURE, _datos_canal())
    parsed = yaml.safe_load(resultado)
    ids = [s["id"] for s in parsed["sources"]]
    assert "yt_nuevo" in ids
    assert ids.index("yt_nuevo") > ids.index("yt_beta")
    assert "yt_alpha" in ids
    assert "social" in parsed
    assert parsed["social"]["shuffle"] is True


def test_agregar_canal_yaml_formato_bloque_canonico():
    resultado = agregar_canal_yaml(YAML_FIXTURE, _datos_canal())
    assert "  - id: yt_nuevo" in resultado
    assert '    name: "Nuevo Canal"' in resultado
    assert '    publisher: "YouTube"' in resultado
    assert "    tier: 3" in resultado
    assert "    type: rss" in resultado
    assert "    category: youtube" in resultado
    assert '    handle: "@nuevo"' in resultado
    assert "    topics: [divulgacion]" in resultado
    assert "    lang: es" in resultado
    assert "    status: verified_2026-07-07" in resultado


def test_agregar_canal_yaml_no_duplica_cabecera_social():
    resultado = agregar_canal_yaml(YAML_FIXTURE, _datos_canal())
    assert resultado.count("social:") == 1
    # La cabecera # ==== de Voces de la red sigue intacta (1 sola vez)
    assert resultado.count("# Voces de la red") == 1


def test_quitar_canal_yaml_borra_bloque_simple():
    resultado = quitar_canal_yaml(YAML_FIXTURE, "yt_alpha")
    parsed = yaml.safe_load(resultado)
    ids = [s["id"] for s in parsed["sources"]]
    assert "yt_alpha" not in ids
    assert "yt_beta" in ids
    assert "social" in parsed


def test_quitar_canal_yaml_borra_bloque_con_notes_sin_dejar_restos():
    resultado = quitar_canal_yaml(YAML_FIXTURE, "yt_beta")
    assert "Feed Atom flaky" not in resultado
    parsed = yaml.safe_load(resultado)
    ids = [s["id"] for s in parsed["sources"]]
    assert "yt_beta" not in ids
    assert "yt_alpha" in ids
    assert "social" in parsed


def test_quitar_canal_yaml_inexistente_lanza():
    with pytest.raises(ValueError):
        quitar_canal_yaml(YAML_FIXTURE, "yt_inexistente")


def test_alta_y_baja_yaml_son_inversas():
    """quitar(agregar(x)) == x exacto (preserva líneas en blanco y cabeceras)."""
    nuevo = agregar_canal_yaml(YAML_FIXTURE, _datos_canal())
    ida_y_vuelta = quitar_canal_yaml(nuevo, "yt_nuevo")
    assert ida_y_vuelta == YAML_FIXTURE


# --- Cirugía pipeline -------------------------------------------------------

def _exec_sources(texto: str) -> list:
    """Compila el pipeline fixture y devuelve DEFAULT_FREE_SOURCES."""
    ns: dict = {}
    exec(texto, ns)
    return ns["DEFAULT_FREE_SOURCES"]


def test_agregar_a_pipeline_inserta_token():
    resultado = agregar_a_pipeline(PIPELINE_FIXTURE, "yt_nuevo")
    assert '"yt_nuevo"' in resultado
    sources = _exec_sources(resultado)
    assert "yt_nuevo" in sources
    assert sources.index("yt_nuevo") > sources.index("yt_beta")
    assert sources.index("yt_nuevo") < sources.index("mastodon")


def test_quitar_de_pipeline_borra_token_compartido():
    # yt_alpha comparte línea con yt_beta en el fixture
    resultado = quitar_de_pipeline(PIPELINE_FIXTURE, "yt_alpha")
    assert '"yt_alpha"' not in resultado
    sources = _exec_sources(resultado)
    assert "yt_alpha" not in sources
    assert "yt_beta" in sources
    assert "mastodon" in sources


def test_quitar_de_pipeline_token_inexistente_no_cambia():
    resultado = quitar_de_pipeline(PIPELINE_FIXTURE, "yt_inexistente")
    assert resultado == PIPELINE_FIXTURE


def test_alta_y_baja_pipeline_son_inversas():
    nuevo = agregar_a_pipeline(PIPELINE_FIXTURE, "yt_nuevo")
    ida_y_vuelta = quitar_de_pipeline(nuevo, "yt_nuevo")
    assert ida_y_vuelta == PIPELINE_FIXTURE


# --- Smoke test con tmp_path (E/S + load_registry) --------------------------

def test_alta_baja_redonda_en_disco_reparseado(tmp_path):
    """Escribe el fixture, hace la cirugía, re-parsea con load_registry y hace
    la baja: el YAML resultante es idéntico al original."""
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(YAML_FIXTURE, encoding="utf-8")

    # Alta: cirugía + escritura
    nuevo_yaml = agregar_canal_yaml(YAML_FIXTURE, _datos_canal())
    yaml_path.write_text(nuevo_yaml, encoding="utf-8")

    _, sources = load_registry(yaml_path)
    yt = [s for s in sources if s.raw.get("category") == "youtube"]
    ids = {s.id for s in yt}
    assert "yt_nuevo" in ids
    assert "yt_alpha" in ids
    assert "yt_beta" in ids
    # El canal nuevo tiene el channel_id correcto (extraído de la URL del feed)
    nuevo = next(s for s in yt if s.id == "yt_nuevo")
    assert canales._extraer_channel_id(nuevo) == "UCcccccccccccccccccccccc"
    assert nuevo.raw["handle"] == "@nuevo"

    # Baja: cirugía + escritura
    borrado = quitar_canal_yaml(nuevo_yaml, "yt_nuevo")
    yaml_path.write_text(borrado, encoding="utf-8")

    _, sources2 = load_registry(yaml_path)
    yt2 = [s for s in sources2 if s.raw.get("category") == "youtube"]
    assert {s.id for s in yt2} == {"yt_alpha", "yt_beta"}

    # Idempotencia textual
    assert borrado == YAML_FIXTURE


def test_alta_canal_rechaza_duplicado_channel_id(tmp_path, monkeypatch):
    """alta_canal detecta channel_id duplicado antes de tocar disco."""
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(YAML_FIXTURE, encoding="utf-8")
    pipeline_path = tmp_path / "pipeline.py"
    pipeline_path.write_text(PIPELINE_FIXTURE, encoding="utf-8")

    monkeypatch.setattr(canales, "CONFIG_PATH", yaml_path)
    monkeypatch.setattr(canales, "PIPELINE_PATH", pipeline_path)
    # load_registry() sin args usa config.CONFIG_PATH (no el de canales);
    # lo mockeamos para que lea el fixture en tmp_path.
    monkeypatch.setattr(canales, "load_registry", lambda path=None: load_registry(yaml_path))
    # Sin red: mockear resolver/verificar para que devuelvan el channel_id de yt_alpha
    monkeypatch.setattr(canales, "resolver_channel_id",
                        lambda entrada, api_key="": ("UCaaaaaaaaaaaaaaaaaaaaaa", "Duplicado"))
    monkeypatch.setattr(canales, "verificar_canal", lambda cid, api_key="": True)
    monkeypatch.setattr(canales, "get_youtube_api_key", lambda: "")

    ok, msg = canales.alta_canal("@duplicado")
    assert not ok
    assert "ya existe" in msg.lower()
    # No se tocó disco
    assert yaml_path.read_text(encoding="utf-8") == YAML_FIXTURE


def test_alta_canal_uc_directo_usa_nombre_para_id(tmp_path, monkeypatch):
    """Con UC directo no hay handle real; el id sale del nombre opcional."""
    yaml_path = tmp_path / "sources.yaml"
    yaml_path.write_text(YAML_FIXTURE, encoding="utf-8")
    pipeline_path = tmp_path / "pipeline.py"
    pipeline_path.write_text(PIPELINE_FIXTURE, encoding="utf-8")

    monkeypatch.setattr(canales, "CONFIG_PATH", yaml_path)
    monkeypatch.setattr(canales, "PIPELINE_PATH", pipeline_path)
    monkeypatch.setattr(canales, "load_registry", lambda path=None: load_registry(yaml_path))
    monkeypatch.setattr(canales, "resolver_channel_id",
                        lambda entrada, api_key="": ("UCcccccccccccccccccccccc", None))
    monkeypatch.setattr(canales, "verificar_canal", lambda cid, api_key="": True)
    monkeypatch.setattr(canales, "get_youtube_api_key", lambda: "")

    ok, msg = canales.alta_canal("UCcccccccccccccccccccccc", nombre="Canal Prueba")

    assert ok, msg
    yaml_nuevo = yaml_path.read_text(encoding="utf-8")
    py_nuevo = pipeline_path.read_text(encoding="utf-8")
    assert "yt_canal_prueba" in yaml_nuevo
    assert '    handle: ""' in yaml_nuevo
    assert '"yt_canal_prueba"' in py_nuevo
