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
  publicaciones.py # publicaciones propias de Sibylla (sección SIBYLLA) desde publicaciones/*.md
  translate.py   # traduce tarjetas de la web (título+snippet) al español con LLM; cache en data/
  articles.py    # extrae el cuerpo de artículos de prensa (trafilatura) para los resúmenes; cache en data/
  resumen.py     # resumen en español por tarjeta (abstract de papers / cuerpo de prensa) con LLM; cache en data/
  canales.py     # gestión de canales de YouTube (alta/baja en sources.yaml + pipeline.py); sin yaml.dump
  admin.py       # servidor admin local (http.server): /metricas + /divulgacion (gestión de canales)
  cli.py         # punto de entrada: python -m sibylla.cli
  templates/     # plantillas Jinja2 (web: index.html.j2; admin: admin_base/dashboard/divulgacion .html.j2)
config/
  sources.yaml   # registro curado de fuentes (tiers, acceso, costo)
  README.md      # documentación del registro y plan de presupuesto de X
locales/         # traducciones JSON (es es la del sitio; en/it/pt se conservan para prompts/digest)
publicaciones/   # noticias propias de Sibylla (Markdown + front-matter; plantilla en _plantilla.md)
tests/           # tests unitarios (pytest, sin red)
  test_models.py    # canonicalize_url, clean_text, NewsItem
  test_relevance.py # _strip_accents, is_relevant, classify_topics
.github/workflows/
  regenerate.yml # automatización: regenera y sube web/ por SSH (cron)
DEPLOY.md        # guía genérica de despliegue + automatización (ver también)
SECCIONES.md     # reglas por sección: fuentes, selección de las 6 tarjetas y orden
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
- **Autoría de commits:** usa la identidad de git ya configurada en el repo/máquina (no la sobreescribas con `-c user.name/user.email`); añade un trailer `Co-Authored-By: <nombre del modelo actual> <noreply@anthropic.com>` (o el dominio que corresponda al proveedor real que esté operando) para dejar constancia de qué agente hizo el cambio. Nunca firmes como un modelo o proveedor distinto del que realmente está ejecutando la sesión.

## Cómo extender

### Añadir un tema
1. En `fetchers.py`, añade una entrada a `TOPIC_CONFIG` (consulta `news` para Google News, `hn` para Hacker News, `arxiv`/`pubmed` si aplica).
2. Añade palabras clave **bilingües (ES/EN, sin tildes)** a `TOPIC_KEYWORDS` para el filtro de relevancia.

### Añadir una fuente a la sección Astronomía
1. En `config/sources.yaml`, añade la fuente con `topics: [astronomia]`.
2. En `pipeline.py`, añade su `id` a `DEFAULT_FREE_SOURCES`.
3. **Fuente chilena (ALMA/CATA/SOCHIAS):** añade su `id` a `ASTRO_PRIORITY_IDS` en `web.py`.
   Tiene slot reservado (cede si >7 días sin contenido nuevo).
4. **Agencia espacial:** añade su `id` a `ASTRO_AGENCY_IDS` en `web.py`.
   Compite por las 3 tarjetas de agencia (máx. 1 por agencia, gana la más reciente).

### Añadir un canal a Divulgación

**Vía dashboard admin (recomendada para canales sueltos):**
1. `python -m sibylla.cli --dashboard` y abre `/divulgacion`.
2. Pega la URL del canal, su `@handle` o el `UC…` directo (y un nombre opcional).
   Con `YOUTUBE_API_KEY` se resuelve vía la API oficial; sin clave, por
   scraping del HTML del canal. Siempre se verifica que tenga videos antes
   de guardarlo. La herramienta edita `config/sources.yaml` y
   `sibylla/pipeline.py` por cirugía de texto (sin `yaml.dump`, que
   destruiría los comentarios) y muestra un banner de "cambios pendientes de
   commit". **No commitea ni pushea**: llega a producción tras commit+push.
3. La lógica vive en `sibylla/canales.py` (funciones puras testeables en
   `tests/test_canales.py`) y el servidor en `sibylla/admin.py`.

**Vía manual (editar archivos directamente):**
1. Resuelve el `channel_id` del canal de YouTube (`UC...`) y verifica que el feed
   `https://www.youtube.com/feeds/videos.xml?channel_id=UC...` responda con entradas.
2. En `config/sources.yaml`, añade una fuente `type: rss`, `category: youtube`,
   `topics: [divulgacion]` y `lang: es` (copia el formato de un bloque `yt_*`
   existente; el dashboard genera exactamente ese formato).
3. En `pipeline.py`, añade su `id` a `DEFAULT_FREE_SOURCES`.
4. No hay que tocar `web.py`: `_select_divulgacion` toma 1 video por canal y
   muestra los 6 canales con video más reciente.

### Publicar una noticia de Sibylla (sección SIBYLLA)
1. Copia `publicaciones/_plantilla.md` con un nombre nuevo **sin** guion bajo
   inicial (p. ej. `2026-07-15-mi-noticia.md`) y rellena el front-matter
   (`titulo` y `fecha` obligatorios; `resumen`, `imagen`, `url`, `publicado`
   opcionales) y el cuerpo (se muestra en el acordeón "Resumen" y, sin `url`,
   en la página propia `pub/<slug>.html` que se autogenera).
2. Commit + push: aparece en el siguiente build del cron. Una `fecha` futura
   pospone la publicación; `publicado: false` la deja en borrador.
3. No hay que tocar código: `sibylla/publicaciones.py` carga la carpeta en
   cada build. Sin publicaciones vigentes, la sección no se renderiza.

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

> 📑 **Reglas de cada sección en un solo lugar:** [SECCIONES.md](SECCIONES.md)
> resume, por sección (Actualidad en Chile, Frontera Digital, Medicina,
> Astronomía, Divulgación en español, RRSS),
> **qué fuentes** la alimentan, **cómo se eligen las 6 tarjetas** y **en qué
> orden**, con un apéndice de "dónde editar cada regla". Empieza por ahí si vas a
> tocar la selección o el alcance de una sección.

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

Los pasos son `0, 2, 4, 6` (configurables vía `data-steps="0,2,4,6"` en el `.card-ctrl`).
El **valor inicial** deriva del onboarding (`sibylla_prefs`, ver "Onboarding de
intereses" abajo): 1er interés 6, el resto 4, RRSS 2, estándar 4 en todas. Sin
prefs (o sin JS) cae al `data-default` (6).

### Reordenar / ocultar secciones (cliente)

Cada sección de noticias se renderiza dentro de un `.bloque[data-topic]` (temas,
astronomía y "Voces de la red", todos hijos del contenedor `#secciones`). En el
encabezado `.tema`, junto al selector de tarjetas, hay tres botones (`.sec-ctrl`):

- **Subir / bajar** (`.sec-up` / `.sec-down`): mueven el bloque entre sus
  vecinos **visibles**; las flechas del primero/último visible quedan `disabled`.
- **Quitar** (`.sec-del`): oculta el bloque (no destructivo).

El orden y la visibilidad persisten en `localStorage` como
`sibylla_layout = {order:[topic...], hidden:[topic...]}` (separado de
`sibylla_cards`). Todo es **client-side**: el HTML se sirve completo y el JS del
pie reordena (`appendChild`) y oculta (`display:none`) sin recargar. El orden
guardado es robusto a rebuilds: temas ausentes se ignoran y los nuevos se anexan
al final (orden original como respaldo).

El botón flotante **Restaurar** (`#restaurar`, abajo a la derecha) aparece solo
cuando hay personalización manual (orden, ocultos o nº de tarjetas distinto al
default) y, al pulsarlo, borra ambas claves manuales y vuelve al estado
**derivado del onboarding** (`sibylla_prefs`, ver siguiente sección) — ya no al
de fábrica.

Textos UI (los 4 locales, por el test de paridad): `sec_up`, `sec_down`,
`sec_remove`, `sec_restore`.

### Onboarding de intereses y modos de visualización (cliente)

En la **primera visita** (sin `sibylla_prefs` en localStorage) se muestra el
overlay `#onboarding`: el visitante toca sus intereses **en orden** (cada chip
recibe un sello con numeral romano = posición en el ranking) y elige el modo de
visualización. También se reabre desde el enlace **"Personalizar"** del menú,
precargado. El resultado se guarda como:

```js
sibylla_prefs = { v:1, mode:"ordenado"|"aleatorio", estandar:bool,
                  ranking:[topic...], known:[topic...] }
```

y define el **estado por defecto** de la portada:

- **Orden y visibilidad:** las secciones elegidas, en el orden del ranking; las
  no elegidas quedan ocultas. RRSS (`social`) y SIBYLLA (`sibylla`) no se
  ofrecen en el onboarding: siempre visibles, al final (SIBYLLA justo antes de
  RRSS; constante `PINNED` en el `<script>` del pie), RRSS con 2 tarjetas y
  SIBYLLA con todas las suyas.
- **Tarjetas:** 1er interés 6, el resto 4, RRSS 2. La opción estándar
  ("un poco de todo") = todas las secciones, orden de fábrica, 4 tarjetas
  (RRSS igual 2). Constantes en el `<script>` del pie: `RANK_FIRST_CARDS`,
  `RANK_REST_CARDS`, `STD_CARDS`, `SOCIAL_CARDS`.
- `known` registra qué secciones se ofrecieron: una sección **nueva** del sitio
  (ausente de `known`) aparece visible al final con 2 tarjetas, sin obligar a
  repetir el onboarding.

Cadena de resolución en runtime: **ajuste manual (`sibylla_cards` /
`sibylla_layout`) → derivado de `sibylla_prefs` → fábrica** (`data-default` /
orden del DOM). Al guardar el onboarding se limpian los ajustes manuales.

**Modos:** el conmutador `#modo-toggle` (sobre `#secciones`) alterna entre
*Ordenado* (la vista clásica por secciones) y *Aleatorio*: las tarjetas se
mueven a `#feed` y se revelan por lotes de 8 con IntersectionObserver (scroll
continuo, mensaje `feed_end` al agotar; mezcla nueva en cada visita). Con
ranking, el feed son **dos fases duras** (`feedQueue`): 1) cada interés con su
mismo tope del modo ordenado (6 el 1º, 4 el resto) + 2 sociales + las
publicaciones SIBYLLA, barajados entre sí; 2) las secciones **no elegidas**
(ocultas en Ordenado), con todas sus cartas, barajadas y al final. En modo estándar / sin ranking es una sola
**mezcla ponderada pareja** (clave `rnd^(1/w)`, RRSS con el peso más bajo). En
feed se ocultan los controles de sección y Restaurar; al volver a Ordenado cada
tarjeta regresa a su rejilla original. El modo persiste en `sibylla_prefs.mode`
y **no** cuenta como "personalización" para Restaurar.

Textos UI (los 4 locales, por el test de paridad): `onb_*`, `mode_*`, `feed_end`.

### Sección "Astronomía" (agencias espaciales + observatorios)

Tras los temas principales, antes de "Voces de la red", se muestra la sección
**Astronomía** con **6 tarjetas curadas** siguiendo reglas de producto:

#### Fuentes (definidas en `config/sources.yaml`, tema `astronomia`)

| Bloque | Fuentes | Tier | Idioma | Lógica |
|--------|---------|------|--------|--------|
| **Chilena (prioritaria)** | ALMA, CATA, SOCHIAS | 1–2 | EN→traducir, ES, ES | 1 slot reservado por fuente; cede si >30 días sin novedad |
| **Agencias** | NASA, ESA, JAXA, CNES, ASI, UKSA (+ futuras) | 1 | EN/FR/IT→traducir | Máx. 1 por agencia; ganan las más recientes |

#### Algoritmo `_select_astronomia` (ver `web.py`)

1. **Bloque chileno (3 cupos):** por cada fuente prioritaria, toma el ítem más
   reciente (≤30 días — estas instituciones publican cada 2–4 semanas). Si una
   fuente no tiene nada en ese rango, cede su cupo a las otras chilenas (pueden
   mostrar >1 ítem). Ventanas en `web.py`: `ASTRO_PRIORITY_FRESH_DAYS = 30`
   (chilenas) y `ASTRO_AGENCY_FRESH_DAYS = 7` (agencias).
2. **Bloque agencias (3 cupos):** 1 representante por agencia (el más reciente).
   Prefiere ≤7 días; con respaldo de más viejas si no hay suficientes; solo
   repite agencia como último recurso.
3. **Relleno cruzado:** si un bloque no llena sus 3, el otro toma los cupos
   sobrantes para mantener siempre 6 tarjetas.
4. **Orden:** tarjeta **1 = chilena más reciente**, tarjeta **2 = agencia más
   reciente**, posiciones **3–6 aleatorias** (semilla por día → estable).

#### Tarjeta APOD

Cada build inyecta la *Astronomy Picture of the Day* de NASA como tarjeta extra en
la sección. La tarjeta **reemplaza la más antigua** de las 6 seleccionadas por
`_select_astronomia`; si hay menos de 6, rellena. Nunca rompe el build: si NASA
no responde, la sección queda con las 6 noticias normales.

- `apod.py`: `build_apod_card(apod, payload) → NewsItem | None` construye la tarjeta
  desde la respuesta de la API de NASA y el payload ya traducido. `source_id = "apod"`
  (fuera de `ASTRO_SOURCE_IDS`) para que el pipeline no lo recoja por RSS.
- `web.py` (`build_all_sites`): llama a `fetch_apod` + `build_apod_i18n` una sola vez;
  reutiliza el payload para (a) construir la tarjeta y (b) escribir `apod-i18n.json`.
  Inyecta el título y la explicación en ES desde el payload (sin token LLM extra).
- **Stellar-View**: la tarjeta APOD se **excluye** de `stellar-news.json` (filtro en
  `build_all_sites` y guardia en `_select_stellar_featured`). La app ya muestra la foto
  del día vía `apod-i18n.json`; su tercera card espera una noticia distinta.
- `static/placeholder-apod.png`: fallback visual para los días que el APOD es un video
  sin miniatura (infrecuente). La tarjeta casi siempre tiene imagen real.
- Tests: `tests/test_apod.py` cubre `build_apod_card`; `tests/test_stellar.py` cubre
  la exclusión de APOD de la selección de Stellar-View.

#### Integración

- `fetchers.py`: `TOPIC_CONFIG['astronomia'] = {}` (pass-through, como `nacional`).
- `pipeline.py`: las 9 fuentes en `DEFAULT_FREE_SOURCES`; NASA y ESA también
  sirven `space` y `general_science`.
- `web.py`: `ASTRO_SOURCE_IDS`, `_is_astro`, `_select_astronomia`; los ítems
  astro se separan de los temáticos normales en `build_all_sites`.
- `cli.py`: `astronomia` en el default de `--topics`; excluido del digest
  temático (como `nacional`).
- Plantilla: bloque `#astronomia` dentro de `#secciones`,
  tras los temas y antes de `#voces` (reordenable/ocultable como los demás).
- Locales: claves `astro_heading`, `astro_subtitle`
  y topic `astronomia` en los 4 idiomas.
- Tests: `tests/test_astronomia.py` (14 casos del selector).

#### Agencias sin feed (documentadas en `sources.yaml`)

CNSA, Roscosmos, ISRO, DLR, CSA, KASA — no exponen RSS/Atom legible
(probadas 2026-06-28). No entran hoy; reevaluar si publican un feed.

### Sección "Divulgación" (videos de YouTube)

Tras Astronomía (y antes de SIBYLLA y "Voces de la red"), se muestra
**Divulgación** con videos de canales de YouTube curados por el usuario. Cada canal es una fuente RSS
Atom nativa (`https://www.youtube.com/feeds/videos.xml?channel_id=UC...`), sin API
key ni fetcher propio.

Reglas:
- `fetchers.py`: `TOPIC_CONFIG['divulgacion'] = {}` (pass-through; los feeds ya
  son curados).
- `sources.yaml`: cada canal usa `topics: [divulgacion]` y `category: youtube`.
- `web.py`: `_is_divulgacion` separa los ítems y `_select_divulgacion` elige hasta
  6 tarjetas: **1 video por canal**, ordenadas por recencia pura.
- No se traducen ni se resumen con LLM. La tarjeta muestra miniatura, sello de
  video `▶`, título original y enlace a YouTube.
- Plantilla: bloque `#divulgacion` dentro de `#secciones`, reordenable/ocultable
  como las demás secciones.
- Tests: `tests/test_divulgacion.py`.

### Sección "SIBYLLA" (publicaciones propias)

Tras Divulgación y antes de "Voces de la red", la sección **SIBYLLA** muestra
las noticias que publica Sibylla misma (anuncios, notas editoriales, novedades
del sitio). No pasa por el pipeline: `sibylla/publicaciones.py` carga los
archivos Markdown de `publicaciones/` en cada build (fallo aislado por archivo).

Reglas:
- **Formato:** front-matter YAML + cuerpo opcional; plantilla comentada en
  `publicaciones/_plantilla.md`. Los archivos `_*.md` se ignoran; `publicado:
  false` = borrador; `fecha` futura = publicación programada (aparece en el
  primer build posterior). Máx. `SIBYLLA_MAX_TOTAL = 6` tarjetas, por fecha
  descendente.
- **Solo aparece si hay algo que mostrar** (`{% if sibylla_cards %}`).
- **No se personaliza:** sin chip en el onboarding, sin selector de tarjetas ni
  botones subir/bajar/quitar; fija al final, justo antes de RRSS (incluso para
  visitantes con un orden manual guardado de antes de que existiera).
- **Modo aleatorio:** sus tarjetas se barajan dentro de la **fase 1** del feed
  (con los intereses del ranking y las 2 sociales).
- **Render:** pill "Sibylla" (`net_sibylla` del locale), sello tier 1, cuerpo
  del archivo en el acordeón "Resumen" (los saltos de párrafo se respetan:
  `.resumen-panel` usa `white-space:pre-line`). **Sin `url` en el front-matter,
  el build genera automáticamente una página propia de la noticia en
  `web/pub/<slug>.html`** (slug = nombre del archivo sin extensión; plantilla
  `templates/pub.html.j2`) y la tarjeta enlaza ahí (título, imagen y botón
  "Original"). Con `url` externa, gana esa y no se genera página. La identidad
  (`dedup_key`) deriva del título: **no cambiar el título** ni renombrar el
  archivo de una publicación ya desplegada (rompe el permalink `pub/<slug>`).
  No se traduce ni se resume con LLM.
- Tests: `tests/test_publicaciones.py`.

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
     `social.house_accounts`), elegidas por **recencia** (último post/repost, vía
     `extra["feed_ts"]`), **ignorando el engagement**, con **diversidad de red**:
     1 por red distinta; misma red solo si ninguna otra aportó. Si hay <2 house,
     rellena con pool orgánico restante.
   - **Fase 3** → rellena huecos de redes que no aportaron nada con el mejor pool
     orgánico hasta `SOCIAL_MAX_TOTAL = 6`.
   - **Fase 4** → baraja las 6 con `random.Random(seed_dia)` si `social.shuffle`.
   - `_social_score` rankea **solo el pool orgánico** (Fases 1 y 3): engagement
     `likes + 2·reposts` en escala log, con decaimiento por frescura y bonus a
     cuentas curadas. Las **house cards (Fase 2) NO lo usan**: van por recencia.

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
| X | `fetch_x` (existente) | `X_BEARER_TOKEN` → Bearer, con tope mensual | `tweets/search/recent`. Su lente se mapea vía `x_topic` a keywords de `TOPIC_CONFIG`; fallback a `social_query` de `sources.yaml`. Cachea en `data/x_recent.json` por **fecha calendario (UTC) + query exacta**: la 1ª corrida del día lee de verdad, las siguientes (p. ej. un `workflow_dispatch` manual el mismo día) reusan el caché sin gastar presupuesto. |

**House posts** (`fetch_house_posts(accounts, include_x)`): consulta el feed de
las cuentas en `social.house_accounts` (incluyendo reposts): Mastodon vía
`accounts/lookup` + `/statuses`, Bluesky vía `getAuthorFeed`, y X vía
`users/by/username` + `users/:id/tweets` (**solo con `--with-x`**, es de pago).
Cada ítem trae `extra["house"]=True` y `extra["feed_ts"]` (hora de la actividad
en la cuenta: el repost/boost si lo es, el post si no; ordena la Fase 2 por
recencia **sin** tocar la fecha visible, que sigue siendo la del post original).
X house: el endpoint de timeline exige `max_results>=5` (piso de 5 lecturas),
respeta el tope mensual (`data/x_usage.json`) y **cachea** el resultado en
`data/x_house.json` por **fecha calendario (UTC)** para no recobrar en builds
del mismo día (mismo criterio que el caché orgánico de arriba).

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
    - { network: x,        handle: SibyllaCl }   # solo con --with-x (de pago)
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

## Documentación privada local (`.security/`)

La carpeta `.security/` está ignorada por git y **solo existe en la máquina del
mantenedor**. Contiene documentación interna (modelo de amenazas y diseño de
producto a futuro). Si existe en tu entorno, **lee `.security/VISION.md` antes
de proponer cambios de arquitectura o diseñar soluciones que deban escalar**:
documenta hacia dónde evoluciona el proyecto y las decisiones de hoy deben ser
compatibles con eso. No cites ni copies su contenido en archivos versionados
(el repo es público). Si la carpeta no existe (CI, clon fresco, worktree),
trabaja normalmente con este archivo como guía.

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
- **Caché diaria de X en CI:** `x_recent.json` (orgánico) y `x_house.json` (house) también se persisten en el host, igual que `x_usage.json`, porque cada corrida de GitHub Actions arranca en una VM limpia — sin esto, un `workflow_dispatch` manual el mismo día del cron volvería a gastar lecturas. Si el host es inalcanzable, cada corrida solo vuelve a leer de la API (nunca rompe el build, en el peor caso gasta de más).
- **Reblogs de Mastodon (boosts):** la API devuelve el `Announce` por fuera y el post original anidado en `reblog` (con `content`/`media_attachments`/contadores propios vacíos, y `uri` apuntando al JSON ActivityStreams). `_mastodon_effective(p)` desreferencia `reblog` para que la tarjeta enlace y muestre el post original (no el repost). Aplica tanto a `fetch_mastodon` como a las house cards. Bluesky ya viene desreferenciado por `getAuthorFeed` (`entry["post"]` = original).
