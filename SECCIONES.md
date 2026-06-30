# SECCIONES.md — Reglas de cada sección de la portada

Referencia única de **cómo se arma cada sección** de noticias de Sibylla: qué
fuentes la pueden alimentar, **cómo se eligen las 6 tarjetas** que terminan
mostrándose y **en qué orden**. Pensado para editar las reglas después: cada
parámetro indica el archivo/constante donde vive.

> Para la arquitectura general del pipeline, ver [AGENTS.md](AGENTS.md). Para el
> registro de fuentes (tiers, acceso, costo), ver [config/sources.yaml](config/sources.yaml).

---

## 0. Visión general

### Las cinco secciones y su orden en la página

La portada (`sibylla/templates/index.html.j2`, contenedor `#secciones`) muestra,
de arriba abajo:

| # | Sección | Tema interno | Cómo se selecciona | Archivo de la lógica |
|---|---------|--------------|--------------------|----------------------|
| 1 | **Nacional** | `nacional` | Embudo heurístico + juez LLM + cuota | `sibylla/nacional.py` |
| 2 | **Frontera Digital** | `ai` *(renombre pendiente)* | Motor temático (score → diversify → top 6) | `sibylla/pipeline.py` |
| 3 | **Medicina** | `medicine` | Motor temático (score → diversify → top 6) | `sibylla/pipeline.py` |
| 4 | **Astronomía** | `astronomia` | Selección curada con cupos reservados | `sibylla/web.py` |
| 5 | **RRSS** ("Voces de la red") | — (redes) | Slots por red + house cards | `sibylla/web.py` |

El orden de los temas temáticos (1–3) sigue el orden pedido en `--topics`
(default `nacional,ai,medicine,astronomia` en `sibylla/cli.py`); Astronomía y
RRSS son bloques especiales que van siempre al final, en ese orden. El usuario
puede **reordenar u ocultar** secciones en el navegador (persiste en
`localStorage`); eso no cambia qué se elige, solo cómo se ve.

### Todas muestran 6 tarjetas

Las cinco secciones se hornean con **6 tarjetas** (`max_por_tema = 6`, valor por
defecto en `build_all_sites` / `build_context` de `sibylla/web.py`).

> ⚠️ **No confundir con `--max-per-source`** (default 10 en `cli.py`): ese límite
> es cuántos ítems se **bajan** por fuente durante el fetch, no cuántas tarjetas
> se muestran.

### Selección ≠ visualización

El HTML se entrega siempre con las 6 tarjetas dentro. Lo que el visitante ve por
defecto y puede ajustar es **client-side** y no toca el algoritmo de selección:

- Selector `− N +` por sección: pasos `0,2,4,6`, valor inicial **6**
  (`data-steps`/`data-default` en la plantilla; los temas temáticos heredan el
  default del JS). Persiste en `localStorage` (`sibylla_cards`).
- Reordenar/ocultar bloques: persiste en `localStorage` (`sibylla_layout`).

Este documento describe **la selección de las 6** (build-time), no el ajuste
visual posterior.

---

## Motor de selección temática (común a Frontera Digital y Medicina)

Frontera Digital y Medicina son **temas temáticos**: usan exactamente el mismo
motor de fetch/ranking. Lo que los diferencia son sus **fuentes y palabras
clave** (abajo, una subsección por sección). El motor:

1. **Fetch por tema** (`sibylla/fetchers.py` → `fetch_source`):
   - **Fuentes por consulta** (`QUERY_SOURCES` = `arxiv_api`, `pubmed_eutils`,
     `hacker_news`, `google_news_rss`): una búsqueda por tema, usando
     `TOPIC_CONFIG[tema]` (categoría de arXiv, query de Google News/HN, flag
     `pubmed`).
   - **Medios RSS/Atom**: se baja el feed una vez y cada ítem se **clasifica por
     relevancia** (`classify_topics` / `is_relevant`, contra `TOPIC_KEYWORDS`).
     Un medio solo entra a un tema si lo declara en su `topics:`
     (`config/sources.yaml`).
2. **Dedupe + cluster** (`pipeline.dedupe`, `cluster.cluster_stories`): une
   duplicados (misma URL/título) y agrupa la misma historia entre medios.
3. **Ranking** (`pipeline._score` → `rank`): puntúa cada ítem por
   **`tier × frescura` + bonus**. Parámetros editables en `sibylla/pipeline.py`:
   - `TIER_WEIGHT = {1: 1.0, 2: 0.7, 3: 0.45}` — peso por confiabilidad.
   - `RECENCY_HALFLIFE_H = 48.0` — vida media de la frescura (h).
   - Bonus: tracción en Hacker News (`points`), corroboración cruzada
     (`related`), prominencia en el feed del medio (`feed_pos`).
4. **Diversidad** (`pipeline.diversify`): **máx. `MAX_PER_SOURCE_TOPIC = 3`**
   ítems de una misma fuente dentro de un tema; el resto va al final (overflow).
   Esta etapa corre para **todos** los temas, pero en la práctica solo decide
   el corte a 6 en los temas que NO están en `CURATED_TOPIC_IDS` (ver abajo).
5. **Corte a 6** (`web._seleccionar_tema`, usada por `_agrupar`/`_rendered_items`):
   - **Frontera Digital y Medicina** (`web.CURATED_TOPIC_IDS = {"ai", "medicine"}`):
     usan el **selector curado** `web._select_curado` (ver siguiente subsección).
   - **El resto de los temas**: corte simple, las **6 primeras** de la lista ya
     rankeada/diversificada.

**Orden de las tarjetas (temas no curados):** por `_score` descendente.

### Selector curado (`web._select_curado`) — Frontera Digital y Medicina

Antes (hasta 2026-06-30), el corte simple dejaba la sección en manos de 1-2
fuentes muy frescas y de alto volumen (p. ej. `techcrunch` + `arxiv_api`
copaban las 6 de Frontera Digital, pese a que otras 8+ fuentes tenían ítems en
el pool). `_select_curado` prioriza **1 tarjeta por fuente distinta**:

1. **Ventana de frescura** (`web.CURATED_FRESH_HOURS = 48.0`, ~2 días): separa
   el pool del tema en FRESCOS (≤48h) y VIEJOS (el resto).
2. **Rondas por fuente** (dentro de cada grupo): ronda 0 toma el mejor ítem
   (por `_score`) de cada fuente distinta; ronda 1 el segundo de cada fuente
   que aún tenga uno; así sucesivamente. Se agotan fuentes nuevas antes de
   repetir ninguna.
3. **Relleno — frescura primero:** las 6 se llenan primero con rondas sobre
   FRESCOS (repitiendo fuente si hace falta) y solo se recurre a VIEJOS si
   FRESCOS no alcanza. Si **todo** el pool está a >48h, se repite la(s) misma(s)
   fuente(s) las veces que sea necesario para llenar 6.
4. **Portada por score puro:** las 6 elegidas se ordenan por `_score`
   descendente — un preprint de arXiv (tier 1, recién publicado) puede ir de
   primero aunque el resto sean medios tier 2.
5. **Las 2 primeras de fuentes distintas:** si tras el orden por score las
   tarjetas 1 y 2 quedan de la misma fuente, se intercambia la 2.ª por la
   siguiente de fuente distinta — salvo que sea imposible (todo el pool es de
   una sola fuente).

Tests: `tests/test_curado.py`.

---

## 1. Frontera Digital

> **Tema interno hoy:** `ai` (etiqueta visible *"Inteligencia artificial"*).
> El renombre a **"Frontera Digital"** y la ampliación de alcance están
> **PENDIENTES** — ver [nota al final de la sección](#pendiente-frontera-digital).

**Alcance objetivo:** inteligencia artificial **+ computación cuántica +
ciberseguridad + actualidad informática** (hardware, software, semiconductores).

**Fuentes posibles** (las que hoy alimentan `ai`):

- Por consulta: `arxiv_api` (categoría `cs.AI`), `google_news_rss`,
  `hacker_news` — definidas en `TOPIC_CONFIG["ai"]` (`sibylla/fetchers.py`).
- Medios RSS que declaran `topics: [ai]` y están en `DEFAULT_FREE_SOURCES`
  (`sibylla/pipeline.py`): `mit_tech_review`, `techcrunch`, `ieee_spectrum`,
  `quanta`, `the_conversation`, `ars_technica`, `the_verge`, `wired`, y
  (desde 2026-06-30, para variedad de fuentes) `krebs`, `bleepingcomputer`
  (ciberseguridad) y `xataka_ia`, `xataka_cuantica`, `hipertextual`
  (español nativo, sin coste de traducción).

**Palabras clave:** `TOPIC_KEYWORDS["ai"]` (`fetchers.py`) — bilingüe ES/EN, sin
tildes; ya incluye ciberseguridad (`cybersecurity`, `ransomware`, `cve`…) y
cómputo cuántico. Es el filtro que decide qué ítems de los medios entran al tema.

**Selección de las 6 y orden:** [selector curado](#selector-curado-web_select_curado--frontera-digital-y-medicina) `web._select_curado` — 1 tarjeta por fuente, ventana de frescura de 2 días, portada por score puro.

<a id="pendiente-frontera-digital"></a>
### ⏳ Pendiente: renombre y ampliación de alcance

Para convertir `ai` en **Frontera Digital** con el alcance ampliado (decisión de
producto; **aún no implementado**), los puntos a tocar serán:

- **Etiqueta:** `topics.ai` en los 4 `locales/*.json` → "Frontera Digital"
  (el test de paridad de locales exige tocar los 4).
- **Alcance/keywords:** ampliar `TOPIC_CONFIG["ai"]` y `TOPIC_KEYWORDS["ai"]`, o
  **fusionar con el tema `computing`** que ya existe (cubre semiconductor, chip,
  `quantum computing`, gpu, hardware/software; categoría arXiv `cs.AR`).
- **Ciberseguridad:** hoy **no** hay tema ni keywords de ciberseguridad — habría
  que añadirlas (p. ej. `security`, `seguridad`, `vulnerab`, `ransomware`,
  `exploit`, `cve`).
- **Fuentes:** revisar `topics:` en `config/sources.yaml` para que medios de
  cómputo/seguridad (p. ej. `ars_technica`, `the_verge`, `wired`) queden
  etiquetados y, si se quieren por defecto, añadirlos a `DEFAULT_FREE_SOURCES`.
- **Default CLI:** si se cambia el `id` del tema, actualizar `--topics` en
  `cli.py` y cualquier referencia (`web.py`, tests).

---

## 2. Medicina

> **Tema interno:** `medicine` (etiqueta *"Medicina"*). Mantiene nombre.

**Alcance:** medicina clínica, fisiología, **edición genética** y afines.

**Fuentes posibles:**

- Por consulta: `pubmed_eutils` (flag `pubmed: True` → busca abstracts en
  PubMed), `google_news_rss`, `hacker_news` — `TOPIC_CONFIG["medicine"]`.
- Medios RSS que declaran `topics: [medicine]` en `DEFAULT_FREE_SOURCES`: p. ej.
  `nature_news`, `phys_org`, `sciencedaily`, `scientific_american`,
  `the_conversation`, `agencia_sinc`.

**Palabras clave:** `TOPIC_KEYWORDS["medicine"]` (`fetchers.py`); ya incluye
términos clínicos, oncológicos, `neuro` y parte de terapia génica.

**Selección de las 6 y orden:** [selector curado](#selector-curado-web_select_curado--frontera-digital-y-medicina) `web._select_curado` — idéntico a Frontera Digital (1 tarjeta por fuente, ventana de 2 días, portada por score puro).

> 💡 **Para cubrir mejor fisiología / edición genética:** se pueden ampliar las
> keywords de `medicine`, o sumar los temas que **ya existen** `biotech`
> (CRISPR, `gene editing`, genómica; arXiv `q-bio.GN`) y `neuroscience`
> (arXiv `q-bio.NC`). Hoy no están en el default de `--topics`.

---

## 3. Astronomía

> **Bloque especial** (`#astronomia`). Lógica en `web._select_astronomia`
> (`sibylla/web.py`). Tests: `tests/test_astronomia.py`.

**Fuentes posibles** (tema `astronomia` en `config/sources.yaml`):

| Bloque | Fuentes (`source_id`) | Constante (`web.py`) | Idioma |
|--------|-----------------------|----------------------|--------|
| **Chileno (prioritario)** | `alma`, `cata`, `sochias` | `ASTRO_PRIORITY_IDS` | EN→traducir, ES, ES |
| **Agencias espaciales** | `nasa`, `esa`, `jaxa`, `cnes`, `asi`, `uksa` | `ASTRO_AGENCY_IDS` | EN/FR/IT→traducir |

Agencias sin feed usable (CNSA, Roscosmos, ISRO, DLR, CSA, KASA) están
documentadas en `config/sources.yaml` pero **no entran** (no exponen RSS/Atom).

**Selección de las 6** (`ASTRO_MAX_TOTAL = 6`, 3 + 3):

1. **Bloque chileno (3 cupos):** 1 reservado por fuente, tomando su ítem más
   reciente de **≤ `ASTRO_PRIORITY_FRESH_DAYS = 30` días**. Si una fuente no
   tiene nada fresco, **cede** su cupo a las otras chilenas.
2. **Bloque agencias (3 cupos):** 1 representante por agencia (el más reciente),
   prefiriendo **≤ `ASTRO_AGENCY_FRESH_DAYS = 7` días**; con respaldo de más
   viejas si faltan; solo repite agencia como último recurso.
3. **Relleno cruzado:** si un bloque no llena sus 3, el otro toma los cupos
   sobrantes — siempre se mantienen 6.

**Orden de las tarjetas:**

- Tarjeta **1** = chilena más reciente.
- Tarjeta **2** = agencia más reciente.
- Tarjetas **3–6** = aleatorias con **semilla por día** (estables dentro del día).

> Añadir una fuente: ver "Añadir una fuente a la sección Astronomía" en
> [AGENTS.md](AGENTS.md) (toca `sources.yaml`, `DEFAULT_FREE_SOURCES` y los sets
> `ASTRO_*` de `web.py`).

---

## 4. Nacional

> **Tema interno:** `nacional`. Selección editorial en `sibylla/nacional.py`
> (se ejecuta en `cli.py` **antes** de construir la web).

A diferencia de los temas temáticos, aquí "relevancia" no es coincidencia de
keywords (todo lo que publican estos medios ya es noticia nacional) sino **valor
noticioso**.

**Fuentes posibles** (`topics: [nacional]` en `config/sources.yaml`):

- **8 medios CL por RSS nativo:** `ciper`, `interferencia`, `diario_uchile`,
  `fast_check_cl`, `lavoz_pucon`, `diario_aysen`, `puranoticia`, `mapuexpress`.
- **`google_news_nacional`:** 6 regionales sin RSS, vía consulta `site:` a Google
  News (El Nortero, El Observatodo, El Tipógrafo, El Martutino, El Vacanudo, El
  Divisadero).

Cada fuente trae `scope: national | regional` (alimenta la cuota regional).

**Selección de las 6** — embudo de dos etapas (`select_nacional`):

1. **Pre-filtro heurístico:** se rankea por `pipeline._score` y se toma un
   shortlist de **`SHORTLIST_N = 30`**.
2. **Juez LLM** (`_judge`): elige y ordena los 6 con una rúbrica de impacto /
   interés público (prompts en `locales/es.json`). **Degrada con elegancia:** sin
   LLM o ante error, cae al top-6 heurístico.
3. **Cuota** (`_apply_quota`): `N_CARDS = 6`, **`MIN_REGIONAL = 2`** (mínimo de
   tarjetas regionales), **`MAX_PER_OUTLET = 2`** (tope por medio).

**Orden de las tarjetas:** el de prioridad del juez (o de la heurística),
**preservado** tras aplicar la cuota.

> Parámetros editables: `N_CARDS`, `MIN_REGIONAL`, `MAX_PER_OUTLET`,
> `SHORTLIST_N` en `sibylla/nacional.py`.

---

## 5. RRSS — "Voces de la red"

> **Bloque especial** (`#voces`). Lógica en `web._select_social`
> (`sibylla/web.py`); config en el bloque `social:` de `config/sources.yaml`.

**Fuentes posibles:**

- **Plazas orgánicas** (1 por red): `mastodon`, `bluesky` (gratis, en
  `DEFAULT_FREE_SOURCES`) y `x_twitter` (**de pago**, solo con `--with-x`).
  Conjunto en `SOCIAL_SOURCE_IDS` (`web.py`).
- **2 house cards:** cuentas propias de Sibylla (`social.house_accounts` en
  `sources.yaml`), vía `fetch_house_posts`.
- **Lentes temáticas** (`social.lenses`): cada red elige una lente al azar por
  día (trend / ia / medicina / chile…), que decide su búsqueda.

**Selección de las 6** (`SOCIAL_MAX_TOTAL = 6`):

1. **Fase 1 — top‑1 por red orgánica:** la mejor de cada red por `_social_score`
   (engagement `likes + 2·reposts` en log, con decaimiento por frescura + bonus a
   cuentas curadas). Hasta 3 slots.
2. **Fase 2 — 2 house cards:** elegidas por **recencia** del último post/repost
   (`extra["feed_ts"]`), **ignorando el engagement**, con diversidad de red (1
   por red distinta).
3. **Fase 3 — relleno:** huecos de redes que no aportaron se llenan con el mejor
   pool orgánico restante, hasta 6.
4. **Fase 4 — barajar:** si `social.shuffle` (default sí), se baraja con
   **semilla por día**.

**Orden de las tarjetas:** barajado (semilla por día → estable en el día). Las
house cards se renderizan **idénticas** a las orgánicas (no llevan badge
"Sibylla").

> Coste de X: pay-per-use con **tope mensual duro**
> (`x_twitter.monthly_read_budget`, default 300). Ver reglas en
> [AGENTS.md](AGENTS.md) y [config/sources.yaml](config/sources.yaml).
> Cómo añadir una red o una lente: secciones correspondientes de AGENTS.md.

---

## 6. Apéndice — dónde editar cada regla

| Quiero cambiar… | Parámetro | Archivo |
|-----------------|-----------|---------|
| Nº de tarjetas por sección temática | `max_por_tema` (default 6) | `sibylla/web.py` |
| Peso por tier en el ranking | `TIER_WEIGHT` | `sibylla/pipeline.py` |
| Cuánto "dura" la frescura | `RECENCY_HALFLIFE_H` | `sibylla/pipeline.py` |
| Máx. por fuente dentro de un tema (temas NO curados) | `MAX_PER_SOURCE_TOPIC` | `sibylla/pipeline.py` |
| Qué temas usan el selector curado (1 por fuente) | `CURATED_TOPIC_IDS` | `sibylla/web.py` |
| Ventana de frescura del selector curado | `CURATED_FRESH_HOURS` | `sibylla/web.py` |
| Qué fuentes entran por defecto | `DEFAULT_FREE_SOURCES` | `sibylla/pipeline.py` |
| Consulta/categoría de un tema | `TOPIC_CONFIG` | `sibylla/fetchers.py` |
| Palabras clave de relevancia | `TOPIC_KEYWORDS` | `sibylla/fetchers.py` |
| Temas por defecto de la corrida | `--topics` (default) | `sibylla/cli.py` |
| Etiquetas visibles de los temas | `web.topics.*` | `locales/*.json` |
| Cupos / ventanas de Astronomía | `ASTRO_*` (`MAX_TOTAL`, `*_FRESH_DAYS`, `*_IDS`) | `sibylla/web.py` |
| Cuota de Nacional | `N_CARDS`, `MIN_REGIONAL`, `MAX_PER_OUTLET`, `SHORTLIST_N` | `sibylla/nacional.py` |
| Nº y barajado de RRSS | `SOCIAL_MAX_TOTAL`, `social.shuffle` | `sibylla/web.py`, `config/sources.yaml` |
| Lentes / cuentas house de RRSS | `social.lenses`, `social.house_accounts` | `config/sources.yaml` |
| Tope de gasto de X | `x_twitter.monthly_read_budget` | `config/sources.yaml` |

Para añadir un tema, una fuente, una red social o una lente, ver las guías
**"Cómo extender"** de [AGENTS.md](AGENTS.md) (este documento no las duplica).
