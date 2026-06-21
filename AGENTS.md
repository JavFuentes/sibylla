# AGENTS.md — guía para agentes y contribuidores

Guía operativa para trabajar en **Sibylla**. Si eres un agente de IA, léela antes de tocar código. Para qué es el proyecto, ver [README.md](README.md).

## Estructura

```
sibylla/
  __init__.py
  models.py      # NewsItem (modelo normalizado) + utilidades de texto/URL
  config.py      # carga config/sources.yaml y .env; rutas ROOT/OUTPUT_DIR
  fetchers.py    # un fetcher por fuente -> List[NewsItem]; relevancia y clasificación
  pipeline.py    # orquesta: seleccionar fuentes -> fetch -> dedupe -> rank -> diversify
  digest.py      # render Markdown determinista (sin IA)
  summarize.py   # resumen con IA (usa llm.py); None si no hay LLM configurado
  llm.py         # capa LLM agnóstica de proveedor (requests puro, sin SDKs)
  i18n.py        # internacionalización simple (JSON sin dependencias)
  web.py         # genera web estática a partir de los ítems del pipeline
  cli.py         # punto de entrada: python -m sibylla.cli
  templates/     # plantillas Jinja2 de la web (index.html.j2)
config/
  sources.yaml   # registro curado de fuentes (tiers, acceso, costo)
  README.md      # documentación del registro y plan de presupuesto de X
locales/         # traducciones JSON (es, en, it, pt)
tests/           # tests unitarios (pytest, sin red)
  test_models.py    # canonicalize_url, clean_text, NewsItem
  test_relevance.py # _strip_accents, is_relevant, classify_topics
.env(.example)   # claves (NO se sube .env); plantilla en .env.example
data/            # estado local (p. ej. x_usage.json) — ignorado por git
output/          # resúmenes generados — ignorado por git
web/             # sitio estático generado — ignorado por git
```

## Convenciones

- **Idioma:** comentarios y docs en **español** (coherente con el resto del repo).
- **Modelo único:** todo fetcher devuelve `list[NewsItem]` (ver `models.py`). Normaliza fechas a UTC *aware*.
- **Fallo aislado:** una fuente que falla solo registra un `log.warning`; **nunca** debe romper la corrida (`fetch_source` envuelve cada fuente en try/except).
- **Tiers de confiabilidad:** 1 = primaria/peer-review, 2 = periodismo, 3 = agregador/discusión. El ranking pondera por tier.
- **Sin SDKs de proveedor:** la capa LLM usa `requests` directo para no atarse a ninguno.
- **Nada de secretos en el código:** las claves se leen de `.env` vía `os.getenv`.

## Cómo extender

### Añadir un tema
1. En `fetchers.py`, añade una entrada a `TOPIC_CONFIG` (consulta `news` para Google News, `hn` para Hacker News, `arxiv`/`pubmed` si aplica).
2. Añade palabras clave **bilingües (ES/EN, sin tildes)** a `TOPIC_KEYWORDS` para el filtro de relevancia.

### Añadir un medio (RSS/Atom)
1. Añádelo en `config/sources.yaml` con `type: rss` (o `atom`) y su `url` de feed.
2. Inclúyelo en `DEFAULT_FREE_SOURCES` (en `pipeline.py`) si quieres que entre por defecto.
   Los medios se bajan una vez y se **clasifican por relevancia** (`classify_topics`), no por consulta.

### Añadir una fuente por API (consulta por tema)
1. Escribe `fetch_xxx(source, query, limit)` en `fetchers.py` devolviendo `list[NewsItem]`.
2. Enrútala en `fetch_source` (añade el `id` a `QUERY_SOURCES` y un branch).

### Añadir un proveedor LLM
1. En `llm.py`, crea una subclase de `LLMProvider` con `complete(system, user, ...)`.
2. Regístrala en `_PROVIDERS`. Si es compatible con OpenAI, reutiliza `OpenAICompatibleProvider`.

## Pipeline (flujo)

`run_pipeline(topics, sources, limit)` → por cada fuente `fetch_source` → `dedupe` (URL canónica / título) → `rank` (`tier × frescura` + bonus HN) → `diversify` (máx. 3 por fuente y tema). El CLer decide si resumir con IA (`summarize_digest`, si hay LLM) o con el render determinista (`render_digest`).

## Web (ver `web.py`)

La web se renderiza desde `sibylla/templates/index.html.j2` (fuente de verdad). **Nunca edites `web/*.html` a mano**; se sobrescriben en cada corrida. Para cambiar diseño/textos:
- **CSS/estructura** → `templates/index.html.j2`
- **Textos UI** → `locales/{es,en,it,pt}.json` (sección `"web"`)
- **Contenido** → lo genera el pipeline automáticamente

### Selector de tarjetas por tema

Cada `.tema` tiene un control `− N +` (valores 0, 2, 4, 6) que el usuario ajusta en el navegador. Persiste en `localStorage` como JSON `{"topic_id": n}`. El JS (`querySelector('.carta')` por rejilla adyacente) oculta/muestra tarjetas sin recargar.

**Implicación para traducción futura:** cuando se traduzcan tarjetas con LLM (estrategia B+A), solo se traducen las N visibles según `sibylla_cards`, ahorrando tokens.

## Seguridad (importante)

- **Nunca** commitees `.env` (claves reales de X / IA). Está en `.gitignore`; mantenlo así.
- `X` es **de pago por uso**. `fetch_x` aplica un **tope mensual duro** (`x_twitter.monthly_read_budget` en `sources.yaml`, uso en `data/x_usage.json`). No lo quites.
- No publiques en X con enlaces ($0.20/post). No subas `output/` ni `data/`.

## Tests (ver [TEST.md](TEST.md))

- **Framework:** pytest (sin dependencias extra, sin red).
- **Qué se testea:** lógica de dominio pura — `canonicalize_url`, `clean_text`, `is_relevant`, `NewsItem`, `dedup_key`.
- **Qué NO se testea (aún):** fetchers HTTP, LLM, CLI (fases posteriores con VCR/mocks).
- **Convenciones:** `@pytest.mark.parametrize` con 3er campo `_desc` para documentar cada caso; un assert por test; sin fixtures ni mocks.
- Si añades keywords a `TOPIC_KEYWORDS`, añade los casos correspondientes en `tests/test_relevance.py`.
- Si modificas `canonicalize_url` o `dedup_key`, añade los casos en `tests/test_models.py`.

## Comandos útiles

```bash
python -m sibylla.cli --help
python -m sibylla.cli --topics ai,medicine --max-per-source 8 --summarize off

# tests
python -m pytest tests/ -v
python -m pytest tests/ -v --cov=sibylla --cov-report=term-missing  # requiere pytest-cov
```

**Antes de commitear:** ejecuta siempre `python -m pytest tests/ -v`. Los tests cubren la lógica de dominio pura (canonicalización de URLs, limpieza de texto, relevancia bilingüe) y deben pasar en < 1s. Si añades keywords, temas o modificas `canonicalize_url`/`dedup_key`, añade los casos correspondientes.

## Gotchas conocidos

- **URLs de Google News:** el formato actual usa tokens opacos no decodificables solo con base64; `resolve_google_news_url` es best-effort (no-op en ese formato). Preferir medios por RSS directo, que dan URL limpia.
- **Relevancia bilingüe:** `is_relevant` quita tildes y compara stems ES/EN; las keywords cortas (≤3) usan límite de palabra (p. ej. `ai` no casa con `airport`).
- **X recent search** exige `max_results ≥ 10`: si el presupuesto restante es < 10, se omite.
