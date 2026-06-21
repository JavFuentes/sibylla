# Sibylla — Registro de fuentes

Este directorio contiene la **configuración curada de fuentes** del proyecto. Es la "lista de la que bebe" Sibylla para investigar temas y armar resúmenes con enlace a la fuente original. Por ahora **no hay código de ingesta**: solo el registro.

- **`sources.yaml`** — el registro canónico, legible y editable a mano.

---

## Principio rector

> Las fuentes se eligen y se **ponderan por confiabilidad (tier)**, no por idioma.
> La ingesta es **multilingüe**; el **resumen final se entrega al usuario en su idioma** (`default_user_language: es`).

La "temática" es solo configuración (tags de `topics` + consultas por tema). Empezamos por **ciencia y tecnología**, pero el mismo esquema escala a cualquier área futura sin reescribir nada.

---

## Esquema de cada fuente

| Campo | Para qué |
|---|---|
| `id` | Identificador estable (no cambiarlo). |
| `name` / `publisher` | Nombre y editor. |
| `tier` | **1** primaria/autoridad · **2** periodismo de calidad · **3** agregador/discusión. Define el peso de confianza. |
| `type` | `api`, `rss`, `atom`, `rss_template`, `paid_api`, `out`. |
| `url` / `endpoint` / `url_template` | De dónde se baja. |
| `topics` | Tags para filtrar/encaminar por tema. |
| `lang` | Idioma de la fuente (`en`, `es`, `multi`). **No** filtra la selección; solo informa. |
| `license` | Qué podemos reusar (CC = republicable; propietaria = solo título+resumen+enlace). |
| `access` | `open` · `needs_key` · `blocked_for_claude_fetcher` · `paywalled` · `paid_api` · `out`. |
| `cost` | `free` o detalle de coste. |
| `status` | `verified_2026-06-20` (probado en vivo) o `known` (estándar, confirmar feed). |
| `notes` | Advertencias y matices. |

### Los tres niveles de confianza (tiers)

- **Tier 1 — Primaria / autoridad.** Revistas peer-review (vía API), preprints, instituciones oficiales (NASA, NIH, WHO, ESA) y agencias de cable. Es lo que se puede afirmar con más seguridad.
- **Tier 2 — Periodismo de calidad.** Medios con estándares editoriales (BBC, Guardian, MIT Tech Review, Nature News, SINC, El País…). Buen contexto y redacción.
- **Tier 3 — Agregadores y discusión.** Google News, GDELT, Hacker News, Reddit, Mastodon, X. Sirven para **descubrir** y medir "qué se discute". **Regla:** nunca se afirma algo apoyándose solo en Tier 3; se corrobora y se enlaza a una fuente Tier 1/2.

> Idea para el ranking (cuando construyamos la ingesta): `score = peso_tier × frescura × corroboración_entre_fuentes`. Una noticia que aparece en varias fuentes Tier 1/2 sube; una que solo está en Tier 3 se marca como "rumor/discusión".

---

## ⭐ Plan de presupuesto: X y señal social (gastar pocos $/mes)

Tienes **$5 cargados** en X y la API es **pay-per-use**: **~$0.005 por post leído**. Es decir, $5 = **1.000 lecturas en total**. Para no quemarlo, X **no** se usa como "manguera" de descubrimiento, sino de forma **quirúrgica**.

**Estrategia de capas (lo barato hace el trabajo pesado):**

1. **Descubrimiento = 100% gratis.** Google News + GDELT + RSS/APIs detectan las noticias. Hacker News + Reddit + Mastodon cubren el "qué se discute". Esto ya da el ~90% del valor sin gastar nada.
2. **X solo para confirmar/enriquecer el top-N.** Una vez al día, tras rankear las historias, se permiten **unas pocas búsquedas dirigidas** en X (p. ej. el titular o el DOI) **solo para las 3–5 historias top**, para ver reacción de expertos.
3. **Cap mensual duro en config.** `monthly_read_budget` en `sources.yaml` (arranca en **300 lecturas/mes ≈ $1.50**). El ingestor lleva un contador y **se detiene** al llegar al tope. $5 ⇒ ~3 meses de colchón.
4. **Cuentas de alta señal, no búsquedas abiertas.** Cuando se use X, apuntar a una **lista curada** de cuentas fiables (revistas, agencias, científicos/periodistas), no a búsquedas amplias que devuelven ruido y multiplican lecturas.
5. **No publicar en X con enlaces.** Publicar un post cuesta $0.015, pero **$0.20 si lleva un enlace**. Si algún día publicamos el resumen, va **sin** enlaces (o por otro canal).
6. **Preferir Bluesky/Mastodon** para el rol de "firehose social": son **gratis y abiertos**. X queda como señal premium opcional.

**Acción pendiente tuya:** entra a tu consola en `developer.x.com` y confirma el **saldo/crédito real** de tu cuenta nueva y tus claves (API key/secret, bearer/OAuth). Con eso afinamos el `monthly_read_budget`.

| Canal social | Coste | Rol en Sibylla |
|---|---|---|
| Hacker News | Gratis | Discusión tech/IA/ciencia dura (sin auth) |
| Reddit | Gratis (no comercial) | Comunidades temáticas; detectar virales |
| Mastodon | Gratis | Científicos en el fediverso (fediscience.org, scholar.social) |
| Bluesky | Gratis (auth) | Firehose abierto alternativo |
| **X** | **Pay-per-use** | **Solo confirmar top-N; con cap mensual** |

---

## Fuentes importantes que quedan FUERA (transparencia)

- **Reuters** — cerró sus RSS públicos en 2020; solo licencia de pago (Reuters Connect). Alcanzable **solo indirectamente** vía Google News/GDELT.
- **Associated Press (AP)** — modelo de licenciamiento; sin feed público útil. Igual: solo indirecto.
- **Science / The Lancet / NEJM / JAMA** — obtenemos **título + abstract + DOI** (vía PubMed/Europe PMC/Crossref), pero **no el texto completo** sin suscripción.
- **Bloomberg / FT / WSJ** — paywall duro (no incluidos en v1).

Esto no rompe el objetivo: para un resumen **con enlace a fuente**, basta con **detectar la noticia y enlazar a una fuente confiable que la cubra** — y eso está cubierto de sobra.

### Nota sobre `blocked_for_claude_fetcher`

Algunos medios (The Verge, Wired, Ars Technica, The Guardian, El País) bloquean **el fetcher de Claude** (están en su blocklist de crawlers de IA), pero **publican RSS/API públicos**. Un ingestor propio del proyecto —con su user-agent y respetando `robots.txt`— normalmente **sí** los lee. Lo **confirmaremos** al construir la ingesta.

---

## Cómo extender el registro

- **Nuevo tema:** añade el tag en `meta.topics` y etiqueta fuentes con él; para Google News/GDELT, define la consulta del tema.
- **Nueva fuente:** copia un bloque de `sources`, rellena los campos y marca `status: known` hasta probarla en vivo.
- **Nueva área (más allá de ciencia/tech):** mismo esquema; solo cambian las fuentes y las consultas.
