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
  cli.py         # punto de entrada: python -m sibylla.cli
config/
  sources.yaml   # registro curado de fuentes (tiers, acceso, costo)
  README.md      # documentación del registro y plan de presupuesto de X
.env(.example)   # claves (NO se sube .env); plantilla en .env.example
data/            # estado local (p. ej. x_usage.json) — ignorado por git
output/          # resúmenes generados — ignorado por git
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

## Seguridad (importante)

- **Nunca** commitees `.env` (claves reales de X / IA). Está en `.gitignore`; mantenlo así.
- `X` es **de pago por uso**. `fetch_x` aplica un **tope mensual duro** (`x_twitter.monthly_read_budget` en `sources.yaml`, uso en `data/x_usage.json`). No lo quites.
- No publiques en X con enlaces ($0.20/post). No subas `output/` ni `data/`.

## Comandos útiles

```bash
python -m sibylla.cli --help
python -m sibylla.cli --topics ai,medicine --max-per-source 8 --summarize off
```

## Gotchas conocidos

- **URLs de Google News:** el formato actual usa tokens opacos no decodificables solo con base64; `resolve_google_news_url` es best-effort (no-op en ese formato). Preferir medios por RSS directo, que dan URL limpia.
- **Relevancia bilingüe:** `is_relevant` quita tildes y compara stems ES/EN; las keywords cortas (≤3) usan límite de palabra (p. ej. `ai` no casa con `airport`).
- **X recent search** exige `max_results ≥ 10`: si el presupuesto restante es < 10, se omite.
