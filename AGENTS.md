# AGENTS.md — guía para agentes y contribuidores

Guía operativa para trabajar en **Sibylla**. Si eres un agente de IA, léela antes de tocar código. Para qué es el proyecto, ver [README.md](README.md).

## Estructura

```
sibylla/
  __init__.py
  models.py      # NewsItem (modelo normalizado) + utilidades de texto/URL
  config.py      # carga config/sources.yaml y .env; rutas ROOT/OUTPUT_DIR
  fetchers.py    # un fetcher por fuente -> List[NewsItem]; relevancia y clasificación
  pipeline.py    # orquesta: seleccionar fuentes -> fetch -> dedupe -> cluster -> rank -> diversify
  cluster.py     # agrupa la MISMA historia entre medios distintos (near-dedup por similitud de título)
  digest.py      # render Markdown determinista (sin IA)
  summarize.py   # resumen con IA (usa llm.py); None si no hay LLM configurado
  llm.py         # capa LLM agnóstica de proveedor (requests puro, sin SDKs)
  i18n.py        # internacionalización simple (JSON sin dependencias)
  web.py         # genera la web estática (una sola página index.html, en español) desde el pipeline
  translate.py   # traduce tarjetas de la web (título+snippet) al español con LLM; cache en data/
  articles.py    # extrae el cuerpo de artículos de prensa (trafilatura) para los resúmenes; cache en data/
  resumen.py     # resumen en español por tarjeta (abstract de papers / cuerpo de prensa) con LLM; cache en data/
  cli.py         # punto de entrada: python -m sibylla.cli
  templates/     # plantillas Jinja2 de la web (index.html.j2)
config/
  sources.yaml   # registro curado de fuentes (tiers, acceso, costo)
  README.md      # documentación del registro y plan de presupuesto de X
locales/         # traducciones JSON (es es la del sitio; en/it/pt se conservan para prompts/digest)
tests/           # tests unitarios (pytest, sin red)
  test_models.py    # canonicalize_url, clean_text, NewsItem
  test_relevance.py # _strip_accents, is_relevant, classify_topics
.github/workflows/
  regenerate.yml # automatización: regenera y sube web/ por SSH (cron)
DEPLOY.md        # guía genérica de despliegue + automatización (ver también)
.env(.example)   # claves (NO se sube .env); plantilla en .env.example
data/            # estado local (x_usage.json, translations.json) — ignorado por git
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
- **No commit ni push sin instrucción explícita:** editar archivos no implica commit ni push. Solo se commitea/pushea cuando el usuario lo pide con palabras como "commitea", "haz push", "sube" o equivalentes. Si un cambio requiere commit para surtir efecto (ej. CI/CD), preguntar primero.

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

`run_pipeline(topics, sources, limit)` → por cada fuente `fetch_source` → `dedupe` (URL canónica / título) → `cluster_stories` (agrupa la misma historia entre medios distintos) → `rank` (`tier × frescura` + bonus HN) → `diversify` (máx. 3 por fuente y tema). El CLer decide si resumir con IA (`summarize_digest`, si hay LLM) o con el render determinista (`render_digest`).

### Agrupación de misma historia (`cluster.py`)

Tras el `dedupe` exacto (misma URL), `cluster_stories` detecta la **misma noticia cubierta por
medios distintos** (URLs distintas, títulos parecidos): conserva un representante (menor tier =
más fiable) y cuelga el resto en `NewsItem.related`, que la web muestra como "**También en: …**"
y el digest como una sub-línea. Es una etapa **pura** (sin red), conservadora a propósito:
similitud de Jaccard de tokens de título (umbral `SIM_THRESHOLD`, mínimo `MIN_SHARED` tokens),
y **solo agrupa fuentes distintas** (mismo medio = historia distinta). No toca `dedup_key`, así
que el cache de traducción sigue válido. **Limitación conocida:** la señal título-Jaccard es
débil para noticias (los titulares de la misma historia se reescriben mucho; los de historias
distintas comparten el vocabulario del tema), por lo que en muchas corridas no agrupa nada. Es
correcto: preferimos no fusionar a fusionar de más. Subir de nivel requiere una señal más fuerte
(entidades / embeddings / el LLM, que ya agrupa en el modo resumen).

## Web (ver `web.py`)

La web se renderiza desde `sibylla/templates/index.html.j2` (fuente de verdad). **Nunca edites `web/*.html` a mano**; se sobrescriben en cada corrida. Para cambiar diseño/textos:
- **CSS/estructura** → `templates/index.html.j2`
- **Textos UI** → `locales/es.json` (sección `"web"`; el sitio es **monolingüe español**)
- **Contenido** → lo genera el pipeline automáticamente

### Sitio monolingüe (español)

El sitio es **una sola página `index.html` en español** (enfocado en Chile). Ya no hay
selector de idioma, página *landing* con auto-detección ni versiones en/en/it/pt: ese
código se retiró. `ALL_LANGS = ["es"]` en `web.py` se conserva como lista solo para no
romper el molde del código. `locales/en,it,pt.json` siguen en el repo porque los usan los
prompts de `summarize`/`digest` y el test de paridad de locales, pero **no se publican**.

Cada tarjeta trae (además del enlace del título a la fuente):
- **Botón "Resumen"** (acordeón inline): resumen en español generado por LLM desde el
  contenido fuente (abstract para papers; cuerpo del artículo para prensa, vía
  `articles.py`/trafilatura). Si no hay LLM o falla el fetch, el botón no aparece.
- **Botón "Original"**: enlace a la fuente (como el título).
El snippet visible de la tarjeta es el de la fuente (traducido al ES); si la fuente no
trae snippet, cae a un recorte del resumen. Ver `resumen.py` y `articles.py`.

### Selector de tarjetas por tema

Cada `.tema` tiene un control `− N +` que el usuario ajusta en el navegador. Persiste en `localStorage` como JSON `{"topic_id": n}`. El JS (`querySelector('.carta')` por rejilla adyacente) oculta/muestra tarjetas sin recargar.

Los valores por defecto son `0, 2, 4, 6` (configurables vía `data-steps="0,2,4,6"` en el `.card-ctrl`).
La sección social usa `data-steps="0,1,2"` (máx. 2 tarjetas). El valor inicial se lee de `data-default`.

### Sección "Voces de la red" (redes sociales) · v2.0

Tras los temas principales, al pie de la página (`#voces`), se muestran **6 tarjetas
con reglas de producto**: 3 plazas de red (Mastodon, Bluesky, X) + 2 tarjetas
de cuentas propias de Sibylla ("house cards"). La lógica se reparte entre `web.py`,
`fetchers.py` y `pipeline.py`.

#### Arquitectura de la sección

1. **Separación e ingesta** (`build_all_sites` → `fetch_source`):
   - Las fuentes en `SOCIAL_SOURCE_IDS` (`web.py`) se fetchean en el pipeline normal
     (`run_pipeline` recorre `DEFAULT_FREE_SOURCES`, que ya incluye `mastodon`, `bluesky`;
     X solo con `--with-x`). Cada una hace UNA llamada API, sin desglosar
     por tema, y devuelve ítems sin `topics` (no son tarjetas de tema).
   - `_is_social(item)` filtra por `source_id` para separar los ítems sociales de
     los temáticos antes de renderizar.

2. **Lentes** (`fetchers.py` `pick_lens` + bloque `social:` en `sources.yaml`):
   - Cada red elige una lente al azar por corrida (semilla por día → estable en el
     día). Las lentes se definen en `config/sources.yaml` → `social.lenses` con peso
     (`weight`). Añadir una sección = añadir una entrada a `lenses`; las
     probabilidades se reparten parejo según el peso.
   - Cada lente tiene campos específicos por red: `mastodon_tag`, `query`,
     `x_topic`. Las búsquedas temáticas (no-`trend`) usan estos campos;
     la lente `trend` consulta el feed "caliente" de cada API.

3. **Selección** (`_select_social` en `web.py`): algoritmo de slots con reglas fijas:
   - **Fase 1** → top‑1 por red orgánica (por `_social_score`) → hasta 3 slots.
   - **Fase 2** → 2 house cards de `fetch_house_posts` (cuentas propias en
     `social.house_accounts`); si hay <2, rellena con pool orgánico restante.
   - **Fase 3** → rellena huecos de redes que no aportaron nada con el mejor pool
     orgánico hasta `SOCIAL_MAX_TOTAL = 6`.
   - **Fase 4** → baraja las 6 con `random.Random(seed_dia)` si `social.shuffle`.
   - `_social_score` se reutiliza tal cual (engagement `likes + 2·reposts`, escala
     log, con decaimiento por frescura y bonus a cuentas curadas).

4. **Renderizado**: el template recibe `social_cards` y genera:
   - Apunte neutro de Sibylla (`social_voice` / `social_voice_text` del locale).
   - 6 tarjetas con badge de red (`.pill`): nombre de la red (`net_*` del
     locale). Las house cards se renderizan **idénticas a las orgánicas**
     (pill de la red, contenido/enlace del post original boosteado, no del
     repost). `extra["house"]` solo guía la selección de slots en `_select_social`,
     no el render.
   - Selector de tarjetas (`data-steps="0,2,4,6"`, `data-default="6"`).
   - Sin posts sociales, la sección entera desaparece (`{% if social_cards %}`).

5. **Traducción**: las tarjetas sociales se incluyen en `_rendered_items` y se
   traducen junto con las normales (estrategia B+A). Sin cambios en `translate.py`.

6. **Degradación elegante**: cada red sin credenciales (falta `.env`) o cuyo fetcher
   falle devuelve `[]` con `log.warning`. Los fallbacks (Fase 3) mantienen las 6
   tarjetas mientras haya al menos 1 red con resultados. Sin `--with-x`, la plaza de
   X y los house de X se omiten y el hueco lo cubre el pool orgánico.

#### Fetchers sociales (ver `fetchers.py`)

Cada uno devuelve `list[NewsItem]` con `extra` uniforme:
`{"kind":"post", "network":<id>, "likes":N, "reposts":N, "author":h, "is_repost":bool}`.

| Red | Fetcher | Auth | Endpoints |
|-----|---------|------|-----------|
| Mastodon | `fetch_mastodon(source, lens, limit)` | Ninguno (instancia pública) | `trends/statuses` o `timelines/tag/{tag}`. Instancia configurable vía `MASTODON_INSTANCE` (def. `mastodon.social`). |
| Bluesky | `fetch_bluesky(source, lens, limit)` | `BLUESKY_IDENTIFIER` + `BLUESKY_APP_PASSWORD` → `createSession` → `accessJwt` cacheado | `getFeed` (What's Hot) o `searchPosts` |
| X | `fetch_x` (existente) | `X_BEARER_TOKEN` → Bearer, con tope mensual | `tweets/search/recent`. Su lente se mapea vía `x_topic` a keywords de `TOPIC_CONFIG`; fallback a `social_query` de `sources.yaml`. |

**House posts** (`fetch_house_posts`): consulta el feed de las cuentas en
`social.house_accounts` (incluyendo reposts): Mastodon vía `accounts/lookup` +
`/statuses`, Bluesky vía `getAuthorFeed`.
Marca `extra["house"]=True`.

#### Configuración (`config/sources.yaml` · bloque `social:`)

```yaml
social:
  shuffle: true
  lenses:                       # 25 % c/u con esta config; escalable
    - { name: trend,    weight: 1, trend: true }
    - { name: ia,       weight: 1, mastodon_tag: ai,  query: …,   x_topic: ai }
    - { name: medicina, weight: 1, mastodon_tag: medicine, query: …, x_topic: medicine }
    - { name: chile,    weight: 1, mastodon_tag: chile, query: …, x_topic: nacional }
  house_accounts:
    - { network: bluesky,  handle: sibylla.cl }
    - { network: mastodon, handle: "@sibylla@mastodon.social" }
```

#### Cómo añadir una red social nueva

1. Define la fuente en `config/sources.yaml` (con `type: api`, `tier: 3`).
2. Escribe su fetcher `fetch_xxx(source, lens, limit)` → `list[NewsItem]` con el
   `extra` uniforme (`network`, `likes`, `reposts`, `author`, `is_repost`).
3. Añade su `source_id` a `SOCIAL_API_SOURCES` y la rama dispatch en `fetch_source`.
4. Añade su `source_id` a `SOCIAL_SOURCE_IDS` en `web.py` y a `DEFAULT_FREE_SOURCES`
   en `pipeline.py`.
5. Añade las claves de locale: `net_xxx` en los 4 archivos de `locales/`.
6. El resto (selección, renderizado, traducción, badges) funciona automáticamente:
   `_select_social` garantiza 1 slot por red, `_tarjeta` renderiza el pill, y si la
   red falla el fallback llena el hueco.

#### Cómo añadir una lente (sección temática nueva)

1. Añade una entrada a `social.lenses` en `sources.yaml` con los campos por red
   (`mastodon_tag`, `query`, `x_topic`).
2. Las probabilidades se reparten automáticamente según `weight`. Si el `x_topic`
   no está en `TOPIC_CONFIG`, X cae a `social_query`.

### Localización de contenido (español) y resúmenes por tarjeta

El sitio es **monolingüe (español)**. La "cáscara" de la web (UI) vive en `locales/es.json`.
El **contenido** de las tarjetas se procesa en dos capas (ambas *build-time*, el visitante
solo descarga HTML ya procesado; la API key es secreto del *operador*):

- **Título + snippet** (`translate.py`): el snippet de la fuente se **traduce al español**
  (para fuentes en inglés) en `translate.py`, horneado en el HTML. Aunque el molde permite
  varios idiomas (`ALL_LANGS`), hoy solo se genera la página `es`.
- **Resumen por tarjeta** (`resumen.py` + `articles.py`): cada tarjeta trae un **botón
  "Resumen"** que despliega (acordeón inline) un resumen en español generado por el LLM:
  - *Papers* (arXiv/PubMed): traduce/resume el **abstract** del feed (robusto, sin red).
  - *Prensa*: `articles.py` extrae el cuerpo con **trafilatura** (`fetch_article_text`) y el
    LLM lo resume. **Frágil** (paywall/bloqueo/JS): si falla, la tarjeta no muestra botón.
  - Si la fuente no trae snippet, el snippet visible cae a un recorte de este resumen.

Claves comunes a ambas capas:
- **Solo se procesan las tarjetas renderizadas** (≤ `max_por_tema` por tema, vía
  `_rendered_items`), nunca el overflow → ahorra tokens.
- **Cache** en `data/translations.json` (título+snippet) y `data/resumenes.json` (resúmenes),
  más `data/articles.json` (cuerpo extraído). Todos ignorados por git, por `dedup_key` con
  `src_title` para invalidar si cambia el título fuente.
- **Degradación elegante:** sin LLM o ante error, se devuelven solo los aciertos del cache y
  el resto cae al idioma original / sin botón de resumen. **Nunca rompe el build.**
- **Prompts** en cada locale bajo `"translate"` y `"resumen"` (sin llaves literales: solo los
  placeholders `{lang}` / `{items_json}`, para no romper `str.format` de `i18n.t`).
- **CLI:** `--translate auto` (defecto) procesa si hay LLM; `--translate off` deja el original.

### Placeholders de fuente (imagen de la tarjeta)

Cuando un ítem no trae imagen propia (`NewsItem.image` es `None`), `_tarjeta()` en
`web.py` asigna `placeholder-{source_id}.png`. Los archivos viven en `static/` y se
copian a `web/` automáticamente en cada build (`_copy_static_assets`). El atributo
`onerror` del `<img>` en el template degrada al gradiente CSS `.ph` si el archivo
no existe, así que un placeholder faltante no rompe nada.

Para añadir un placeholder nuevo: crear `static/placeholder-{id}.png` (16:9, ej.
640×360). Sin tocar código. Si el `source_id` tiene un nombre poco amigable (ej.
`pubmed_eutils`), se puede nombrar distinto y añadir un mapeo explícito al dict
`SOURCE_PLACEHOLDERS` en `web.py`; pero la convención directa cubre la mayoría de
los casos.

### Despliegue y automatización

`web/` es estático e ignorado por git: desplegar = **regenerar** y **subir** su contenido a la raíz
pública de cualquier hosting. Guía genérica (sin proveedor concreto) en **[DEPLOY.md](DEPLOY.md)**, y
workflow de GitHub Actions en `.github/workflows/regenerate.yml`. Las claves van solo como *secrets*
de CI o en `.env` local — **nunca** en el repo (es público).

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
- **Tope mensual de X en CI:** `x_usage.json` se **persiste en el host** (igual que `runs.json`): el workflow lo descarga antes de generar y lo sube después (best-effort, no aborta el deploy). Así el `monthly_read_budget` sí frena en CI. Es un contador de gasto, no historial: si se pierde, el mes solo reinicia a `reads=0` (no corrompe nada).
- **Reblogs de Mastodon (boosts):** la API devuelve el `Announce` por fuera y el post original anidado en `reblog` (con `content`/`media_attachments`/contadores propios vacíos, y `uri` apuntando al JSON ActivityStreams). `_mastodon_effective(p)` desreferencia `reblog` para que la tarjeta enlace y muestre el post original (no el repost). Aplica tanto a `fetch_mastodon` como a las house cards. Bluesky ya viene desreferenciado por `getAuthorFeed` (`entry["post"]` = original).
