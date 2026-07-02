# PLAN — Sección «Divulgación científica» (videos de YouTube)

> Documento de implementación para un agente. Self-contained: no asume haber
> visto la conversación que lo originó. Para la arquitectura general, ver
> [AGENTS.md](AGENTS.md); para las reglas de cada sección, [SECCIONES.md](SECCIONES.md).
>
> **Estado 2026-07-01:** la sección ya está implementada y visible en producción,
> pero quedan problemas abiertos con feeds YouTube que fallan en GitHub Actions y
> con selección de solo 5 tarjetas en una corrida. Ver **§12** antes de tocar código.

---

## 0. Qué se quiere

Una sección nueva en la portada, **«Divulgación científica»**, que —al menos al
comienzo— muestra **videos de YouTube** de un conjunto de **canales curados a
mano** por el usuario. Se actualiza sola en cada build mostrando lo más reciente.

### Decisiones de producto ya tomadas (cerradas)

| # | Decisión | Elegido |
|---|----------|---------|
| 1 | **Selección de las 6 tarjetas** | **1 video por canal**, los **6 canales con el video más reciente**. Sin repetir canal. |
| 2 | **Reproducción** | **Miniatura que enlaza a YouTube** (abre en YouTube). NO iframe incrustado. |
| 3 | **Marca visual** (el sitio usa sellos I/II/III de confiabilidad) | **Insignia de video ▶** en vez del sello de tier. No se toca el sistema de sellos. |
| 4 | **Idioma de títulos** | **No traducir** (canales hispanohablantes). Cero gasto de LLM en esta sección. |

### Principio de diseño

Es una **sección especial curada**, estructuralmente idéntica a **Astronomía** y
a **«Voces de la red»**: NO es un tema por palabras clave. Se alimenta del
pipeline normal (cada canal es una fuente RSS), pero sus ítems se **separan**
antes de renderizar y se eligen con un **selector propio**. El nombre del tema
interno es **`divulgacion`** (genérico, para poder sumar fuentes no-YouTube en el
futuro sin renombrar nada).

---

## 1. Por qué YouTube encaja sin fetcher nuevo

Cada canal de YouTube expone un **feed Atom público y gratuito** (sin API key,
sin tope de uso):

```
https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxxxxxxxxxxxxxxxxxxxx
```

Devuelve los **~15 videos más recientes** del canal, con `<title>`, `<link>` (URL
`watch?v=`), `<published>` y `<media:group>` (incluye `<media:thumbnail>` y
`<media:description>`). El fetcher genérico existente lo parsea tal cual:

- `fetch_generic_rss` ([`sibylla/fetchers.py`](sibylla/fetchers.py)) ya extrae
  `link` → URL del video, `published_parsed`, y la miniatura vía `_rss_image`
  (que lee `e.media_thumbnail`). **No hay que escribir ningún fetcher nuevo.**
- `canonicalize_url` conserva el parámetro `v` (no está en la lista de tracking),
  así que el `dedup_key` por video es estable y único.
- La miniatura YouTube (`i.ytimg.com/vi/<id>/hqdefault.jpg`) carga con el
  `referrerpolicy="no-referrer"` que ya trae el `<img>` de la plantilla.

> ⚠️ **Resolver el `channel_id`.** Los canales se conocen por su *handle*
> (`@QuantumFracture`) o URL, pero el feed necesita el id `UC…`. Ver §9 para cómo
> resolverlo y verificar cada feed en vivo **antes** de darlo por bueno.

---

## 2. Resumen de archivos a tocar

| Archivo | Cambio |
|---------|--------|
| `config/sources.yaml` | Añadir `divulgacion` a `meta.topics`; añadir **una fuente por canal** (`type: rss`, `category: youtube`, `topics: [divulgacion]`). |
| `sibylla/fetchers.py` | Añadir `"divulgacion": {}` a `TOPIC_CONFIG` (pass-through, como `astronomia`). |
| `sibylla/pipeline.py` | Añadir los `id` de los canales a `DEFAULT_FREE_SOURCES`. |
| `sibylla/web.py` | `_is_divulgacion`, `_select_divulgacion`, constantes `DIVULGACION_*`; separar los ítems en `build_all_sites`; pasar `divulgacion_items` por `build_context`/`render_html`; flag `is_video` en `_tarjeta`. |
| `sibylla/templates/index.html.j2` | Bloque `#divulgacion`; insignia ▶ en la macro `tarjeta`; CSS del overlay. |
| `sibylla/cli.py` | Añadir `divulgacion` al default de `--topics`; excluirlo del digest y de `items_topic`. |
| `locales/es.json` + `en/it/pt.json` | Claves `divulgacion_heading`, `divulgacion_subtitle`, `topics.divulgacion`, `video_label`. (Las 4, por el test de paridad.) |
| `tests/test_divulgacion.py` | Tests unitarios del selector (sin red). |
| `AGENTS.md`, `SECCIONES.md` | Documentar la sección (docs vivas del repo). |

---

## 3. Ingesta (sources.yaml + fetchers + pipeline)

### 3.1 `config/sources.yaml`

1. Añadir el tema a la lista de referencia `meta.topics`:
   ```yaml
   topics: [nacional, general_science, general_tech, ai, computing, space, astronomia,
            physics, biotech, medicine, climate, energy, neuroscience, divulgacion]
   ```
2. Añadir, en un bloque comentado nuevo (al estilo de las agencias espaciales),
   **una entrada por canal**. Plantilla:
   ```yaml
   # =============================================================================
   # DIVULGACIÓN CIENTÍFICA — canales de YouTube curados a mano. Cada canal es una
   # fuente RSS (feed Atom nativo de YouTube, gratis, sin key). Se muestran como
   # tarjetas de video en la sección homónima (1 por canal, 6 más recientes).
   # =============================================================================
     - id: yt_quantumfracture
       name: "QuantumFracture"          # se muestra como nombre del canal en la tarjeta
       publisher: "YouTube"
       tier: 3                           # no se usa para el sello (se pinta ▶), pero NewsItem lo exige
       type: rss                         # feedparser parsea el Atom de YouTube
       category: youtube                 # marca de canal (para claridad/futuros filtros)
       url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC..."
       topics: [divulgacion]             # IMPORTANTE: SOLO divulgacion (ver nota)
       lang: es
       license: "solo miniatura + título + enlace al video"
       access: open
       cost: free
       status: known                     # → verified_YYYY-MM-DD tras probar el feed en vivo
       notes: "Canal de divulgación; feed Atom nativo (15 últimos videos)."
   ```

   > **Nota crítica:** cada canal debe declarar **únicamente** `topics:
   > [divulgacion]`. Así el dispatcher RSS (`fetch_source`) etiqueta sus videos
   > solo con ese tema y nunca se cuelan en Frontera Digital/Medicina/etc. (el
   > tema `divulgacion` no tiene keywords → `is_relevant` deja pasar todo, igual
   > que `astronomia` y `nacional`).

### 3.2 `sibylla/fetchers.py`

Añadir a `TOPIC_CONFIG` la entrada pass-through (junto a `nacional` y `astronomia`):

```python
# Divulgación científica: como `astronomia`, las fuentes (canales de YouTube
# curados) ya publican solo contenido relevante. Config vacía = pass-through.
"divulgacion":    {},
```

No hace falta nada más en fetchers: el feed entra por la rama genérica
`source.type in ("rss", "atom")` de `fetch_source`.

### 3.3 `sibylla/pipeline.py`

Añadir los `id` de los canales a `DEFAULT_FREE_SOURCES`, en su propio bloque:

```python
# Divulgación científica: canales de YouTube (1 tarjeta por canal)
"yt_quantumfracture", "yt_dateunvlog", "yt_...",
```

---

## 4. Selección (`sibylla/web.py`)

### 4.1 Separación de los ítems

Igual que `_is_astro`/`_is_social`, añadir un detector. Se detecta **por tema**
(no por una lista de ids hardcodeada), para que **sumar un canal toque solo
`sources.yaml` + `DEFAULT_FREE_SOURCES`** y nunca `web.py`:

```python
def _is_divulgacion(item: NewsItem) -> bool:
    """True si el ítem es un video de la sección Divulgación científica."""
    return "divulgacion" in item.topics
```

### 4.2 Constantes

```python
# =============================================================================
# Sección Divulgación científica — videos de YouTube, 1 por canal
# =============================================================================
DIVULGACION_MAX_TOTAL = 6
# Ventana blanda de frescura: un canal dormido más de esto no surtirá un video
# antiguo. Generosa a propósito (algunos divulgadores publican trimestralmente).
DIVULGACION_FRESH_DAYS = 365
```

### 4.3 Algoritmo `_select_divulgacion`

Regla elegida: **1 video por canal, los 6 canales con el video más reciente,
ordenados del más nuevo al más viejo.**

```python
def _select_divulgacion(items: list[NewsItem]) -> list[NewsItem]:
    """Selecciona hasta 6 tarjetas de video: 1 por canal, los canales con el
    video más reciente, ordenadas por recencia (más nuevo primero).

    - Agrupa por canal (source_id) y toma el video MÁS RECIENTE de cada uno.
    - Descarta videos más viejos que DIVULGACION_FRESH_DAYS.
    - Ordena los canales por la fecha de su video más reciente (desc) y toma 6.
    - Si hay <6 canales con video fresco, devuelve los que haya (la sección
      sigue mostrándose mientras haya ≥1; el template la oculta si queda vacía).
    """
    from datetime import timedelta
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DIVULGACION_FRESH_DAYS)

    def _recency(it: NewsItem):
        return it.published or datetime.min.replace(tzinfo=timezone.utc)

    # Mejor (más reciente) video por canal, dentro de la ventana de frescura.
    best_by_channel: dict[str, NewsItem] = {}
    for it in items:
        if _recency(it) < cutoff:
            continue
        cur = best_by_channel.get(it.source_id)
        if cur is None or _recency(it) > _recency(cur):
            best_by_channel[it.source_id] = it

    picks = sorted(best_by_channel.values(), key=_recency, reverse=True)
    return picks[:DIVULGACION_MAX_TOTAL]
```

> **Nota sobre el orden:** a diferencia de Astronomía (que baraja las posiciones
> 3–6 con semilla por día), aquí el orden es **recencia pura**: la sección
> comunica «lo último de tus canales», así que el video más nuevo va primero.
> No requiere semilla.

---

## 5. Renderizado

### 5.1 `_tarjeta` (web.py) — flag `is_video`

`_tarjeta` debe aceptar un parámetro nuevo y exponerlo a la plantilla. Para los
videos: **no traducir** (se llama con `translations=None`), **sin botón Resumen**
(`has_resumen=False` natural), y marcar `is_video=True`.

```python
def _tarjeta(it, months, no_date, translations=None, resumenes=None,
             is_video: bool = False) -> dict:
    ...
    card = { ... }                 # como hoy
    card["is_video"] = is_video
    if is_video:
        # Acento de tarjeta propio (el sello se reemplaza por ▶ en la plantilla).
        card["seal_color"] = "#5EE6E0"   # cian "futuro" (tunable)
    return card
```

### 5.2 `build_context` / `render_html` / `build_all_sites`

Threadear `divulgacion_items` como ya se hace con `astro_items`/`social_items`:

1. **`build_all_sites`**:
   ```python
   # Separar ítems: normales / astro / social / divulgación.
   normal_items = [it for it in items
                   if not _is_social(it) and not _is_astro(it) and not _is_divulgacion(it)]
   divulgacion_raw = [it for it in items if _is_divulgacion(it)]
   divulgacion_top = _select_divulgacion(divulgacion_raw)
   ```
   - Pasar `divulgacion_items=divulgacion_top` a `render_html`.
   - **NO** incluir `divulgacion_top` en `_build_translations` ni en
     `build_resumenes` (decisión 4: no traducir; y trafilatura sobre una página
     de YouTube no da resumen útil). Es decir: dejar `_rendered_items(normal_items,
     topics, max_por_tema, social_top, astro_top)` **sin** divulgación.

2. **`build_context`** y **`render_html`**: añadir el parámetro
   `divulgacion_items` y construir:
   ```python
   divulgacion_cards = []
   if divulgacion_items:
       divulgacion_cards = [_tarjeta(it, months, no_date, is_video=True)
                            for it in divulgacion_items]
   ```
   y devolver `"divulgacion_cards": divulgacion_cards` en el contexto.

### 5.3 Plantilla `index.html.j2`

**(a)** Macro `tarjeta`: insignia ▶ y sustitución del sello. Donde hoy está la
imagen y el sello:

```jinja
{% if c.image %}
  <div class="carta-img">
    <img src="{{ c.image }}" alt="" loading="lazy" referrerpolicy="no-referrer"
         onerror="this.parentNode.classList.add('ph');this.remove();">
    {% if c.is_video %}<span class="play" aria-hidden="true">
      <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>{% endif %}
  </div>
{% else %}<div class="carta-img ph" aria-hidden="true">
  {% if c.is_video %}<span class="play" aria-hidden="true">
    <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z"/></svg></span>{% endif %}
</div>{% endif %}
```

En la línea `.fuente`, reemplazar el sello por la insignia de video cuando aplique:

```jinja
<div class="fuente">
  {% if c.is_video %}<span class="sello sello-video" title="{{ t.video_label }}">▶</span>
  {% else %}<span class="sello {{ c.seal_class }}">{{ c.seal_roman }}</span>{% endif %}
  {{ c.source_name }} <span class="sep">·</span> {{ c.date }}
</div>
```

**(b)** Bloque de la sección, dentro de `#secciones`, **después de `#astronomia`
y antes de `#voces`** (es reordenable/ocultable por el usuario; el JS del pie ya
incluye cualquier `.bloque[data-topic]` nuevo en `ORIGINAL` automáticamente):

```jinja
{% if divulgacion_cards %}
  <div class="bloque bloque-especial" id="divulgacion" data-topic="divulgacion">
    <div class="tema" data-topic="divulgacion"><h3>{{ t.divulgacion_heading }}</h3><span class="raya"></span>
    <span class="tema-ctrls"><span class="card-ctrl" data-steps="0,2,4,6" data-default="6">
      <button class="card-ctrl-btn" aria-label="{{ t.cards_less }}" data-dir="-1">&minus;</button>
      <span class="card-ctrl-val">6</span>
      <button class="card-ctrl-btn" aria-label="{{ t.cards_more }}" data-dir="1">+</button>
    </span>{{ sec_ctrl() }}</span></div>
    <div class="rejilla">
      {% for c in divulgacion_cards %}{{ tarjeta(c, t.also_in) }}{% endfor %}
    </div>
  </div>
{% endif %}
```

**(c)** CSS (junto a los estilos de `.carta-img`):

```css
.carta-img{ position:relative; }
.carta-img .play{
  position:absolute; inset:0; margin:auto;
  width:52px; height:52px; border-radius:50%;
  display:flex; align-items:center; justify-content:center;
  background:rgba(8,11,20,.55); border:1px solid var(--oro);
  box-shadow:0 2px 14px rgba(0,0,0,.4); pointer-events:none;
}
.carta-img .play svg{ width:22px; height:22px; fill:var(--marfil); margin-left:3px; }
.sello.sello-video{ background:transparent; border-color:var(--cian); color:var(--cian); }
```

> La insignia ▶ es decorativa (`pointer-events:none`): toda la tarjeta ya enlaza
> al video por el título y el botón «Original». No se incrusta iframe (decisión 2).

---

## 6. CLI (`sibylla/cli.py`)

1. Default de `--topics`:
   ```python
   parser.add_argument("--topics", default="nacional,ai,medicine,astronomia,divulgacion", ...)
   ```
2. Excluir del digest (es de ciencia/tecnología; los videos van solo a la web),
   igual que `nacional`/`astronomia`:
   ```python
   from .web import _is_social, _is_astro, _is_divulgacion
   items_topic = [it for it in items
                  if not _is_social(it) and not is_nacional(it)
                  and not _is_astro(it) and not _is_divulgacion(it)]
   topics_sci = [tp for tp in topics if tp not in ("nacional", "astronomia", "divulgacion")]
   ```

---

## 7. Locales (4 archivos: es / en / it / pt)

El test de paridad exige el **mismo conjunto de claves** en los 4. Añadir bajo
`"web"`:

| Clave | es | en | it | pt |
|-------|----|----|----|----|
| `divulgacion_heading` | "Divulgación científica" | "Science outreach" | "Divulgazione scientifica" | "Divulgação científica" |
| `divulgacion_subtitle` | "Lo último de tus canales de divulgación" | "The latest from your outreach channels" | "Le ultime dai tuoi canali di divulgazione" | "O mais recente dos seus canais de divulgação" |
| `video_label` | "Video" | "Video" | "Video" | "Vídeo" |

Y en el mapa `"topics"` de cada locale:
```json
"divulgacion": "Divulgación científica"
```
(traducido en en/it/pt). El sitio es monolingüe español, pero las 4 deben existir
por el test de paridad (en/it/pt los usan los prompts y ese test).

---

## 8. Tests (`tests/test_divulgacion.py`)

Seguir las convenciones del repo (ver [TEST.md](TEST.md)): pytest sin red,
`@pytest.mark.parametrize` con 3er campo `_desc`, un assert por test, sin mocks.
Probar **`_select_divulgacion`** con `NewsItem` sintéticos:

1. **>6 canales** → devuelve 6, **todos de canal distinto**, ordenados por
   recencia desc (el más nuevo primero).
2. **Un canal con varios videos recientes** → ese canal aparece **una sola vez**,
   con su video más nuevo.
3. **<6 canales** → devuelve exactamente esos (p. ej. 3 canales → 3 tarjetas).
4. **Frescura:** un video más viejo que `DIVULGACION_FRESH_DAYS` queda excluido;
   si era el único de su canal, ese canal no aparece.
5. **Vacío** → `[]`.
6. (Opcional) Ítem sin `published` no rompe y queda al final / excluido por cutoff.

Recordar: si se añaden claves a `locales/`, el test de paridad de locales debe
seguir en verde (de ahí que haya que tocar los 4 archivos).

**Antes de terminar:** `python -m pytest tests/ -v` (debe pasar en <1s).

---

## 9. Canales — RESUELTOS Y VERIFICADOS (2026-06-30)

Los 37 canales del usuario, con su `channel_id` `UC…` resuelto y su feed Atom
**probado en vivo** (HTTP 200 con `<entry>`) el 2026-06-30. Las entradas
`sources.yaml` listas para pegar están en el **Apéndice A**; la lista de ids para
`DEFAULT_FREE_SOURCES`, en el **Apéndice B**.

| Handle | `id` de fuente | Nombre del canal (del feed) |
|--------|----------------|-----------------------------|
| @jodisea | `yt_jodisea` | Jodisea \| El mundo de las Odiseas |
| @RadientNews | `yt_radientnews` | Radient News |
| @jefillysh | `yt_jefillysh` | jefillysh |
| @ElRobotdePlaton | `yt_elrobotdeplaton` | El Robot de Platón |
| @JesúsGMaestro | `yt_jesusgmaestro` | Jesús G. Maestro |
| @QuantumFracture | `yt_quantumfracture` | QuantumFracture |
| @pildorasinformaticas | `yt_pildorasinformaticas` | pildorasinformaticas |
| @exoplanetas | `yt_exoplanetas` | EXOPLANETAS Noticias Ciencia y Tecnología |
| @ROBOTITUS | `yt_robotitus` | Noticias Robotitus |
| @Ecosdeunmundoestrellado | `yt_ecosdeunmundoestrellado` | Ecosdeunmundoestrellado |
| @FaztTech | `yt_fazttech` | Fazt |
| @psicovlog | `yt_psicovlog` | Psico Vlog |
| @LaGataDeSchrödinger | `yt_lagatadeschrodinger` | La gata de Schrödinger |
| @SizeMatters | `yt_sizematters` | SizeMatters |
| @curiosamente | `yt_curiosamente` | CuriosaMente |
| @IFTMadrid | `yt_iftmadrid` | Instituto de Física Teórica IFT |
| @novagea | `yt_novagea` | NovaGea |
| @astrumespanol | `yt_astrumespanol` | Astrum Español |
| @raqueldelamorenaoficial | `yt_raqueldelamorenaoficial` | Raquel de la Morena |
| @Ter | `yt_ter` | Ter |
| @darinmex | `yt_darinmex` | Darin McNabb |
| @Candeliousfang | `yt_candeliousfang` | Candeliousfang |
| @jefidos | `yt_jefidos` | Carolina Jefillysh |
| @MatesMike | `yt_matesmike` | Mates Mike |
| @PonteBata | `yt_pontebata` | Ponte Bata |
| @CienciaDeSofa | `yt_cienciadesofa` | CienciaDeSofa |
| @BitBoss | `yt_bitboss` | BitBoss |
| @cinematixfilms | `yt_cinematixfilms` | Cinematix |
| @AnatomíaHumanayDisección | `yt_anatomiahumanaydiseccion` | Anatomía Humana y Disección |
| @IFTWebinars | `yt_iftwebinars` | IFT Webinars |
| @astrovlog | `yt_astrovlog` | astrovlog |
| @Lahiperactina | `yt_lahiperactina` | La Hiperactina |
| @AlvaMajo | `yt_alvamajo` | Alva Majo |
| @Javier_Garcia | `yt_javier_garcia` | Javier Garcia |
| @AntroporamaDivulgacion | `yt_antroporamadivulgacion` | Antroporama |
| @deborahciencia | `yt_deborahciencia` | deborahciencia |
| @midudev | `yt_midudev` | midudev |

> **Notas para el usuario (no bloquean la implementación):**
> - **`@jefillysh` y `@jefidos`** son de la misma creadora (Jefillysh / "Carolina
>   Jefillysh"): son **dos canales distintos** pero del mismo proyecto. Ambos
>   quedan incluidos; quitar uno si se prefiere no duplicar autor.
> - El roster es **divulgación amplia**, no solo "ciencia" estricta: hay programación
>   (`midudev`, `Fazt`, `pildorasinformaticas`, `BitBoss`), cine (`Cinematix`),
>   filosofía/letras (`Jesús G. Maestro`, `Darin McNabb`), videojuegos/humor
>   (`Alva Majo`), arte/arquitectura (`Ter`). Encaja con un título de sección
>   "Divulgación" a secas; si se quiere mantener "Divulgación **científica**",
>   valorar podar los no-científicos. **Decisión del usuario**, no del implementador.

### Cómo se resolvió (para reproducir o añadir canales luego)

- `channel_id`: se abrió `https://www.youtube.com/@<handle>` y se extrajo el
  `channel/UC…` canónico del HTML. Los handles con tilde (`@JesúsGMaestro`,
  `@LaGataDeSchrödinger`, `@AnatomíaHumanayDisección`) requieren **percent-encoding**
  de la URL (UTF-8 crudo da 404).
- Verificación: el feed `https://www.youtube.com/feeds/videos.xml?channel_id=UC…`
  debe responder **200** con `<entry>`. (El parámetro legacy `?user=` suele fallar;
  usar siempre `?channel_id=`.)

---

## 10. Criterios de aceptación (checklist)

- [ ] `python -m sibylla.cli --topics divulgacion --sources yt_<uno> --html` genera
      `web/index.html` con la sección **Divulgación científica** y tarjetas de video.
- [ ] Cada tarjeta: miniatura del video + ▶, título original (sin traducir),
      nombre del canal, fecha; el título y el botón «Original» enlazan al
      `watch?v=`. **Sin** botón «Resumen». **Sin** sello I/II/III (lleva ▶).
- [ ] Se muestran **≤6** tarjetas, **1 por canal**, **más reciente primero**.
- [ ] Un canal que sube varios videos **no** ocupa más de 1 tarjeta.
- [ ] La sección se **oculta** sola si no hay videos; es **reordenable/ocultable**
      en el navegador como las demás (persiste en `localStorage`).
- [ ] **Cero** llamadas LLM atribuibles a esta sección (ni traducción ni resumen).
- [ ] `python -m pytest tests/ -v` en verde (incluido `test_divulgacion.py` y la
      paridad de locales).
- [ ] `AGENTS.md` (apartado «Cómo extender» → «Añadir un canal a Divulgación
      científica») y `SECCIONES.md` (sección nueva) actualizados.

---

## 11. Notas y decisiones diferidas (no bloquean)

- **Incrustar el video (iframe):** descartado por ahora (decisión 2). Si se
  quisiera después, sería un `facade` que carga `youtube-nocookie` solo al clic,
  para no penalizar rendimiento ni privacidad.
- **Conteo en el hero / JSON-LD:** las tarjetas de video, como las de astro y
  social, no entran hoy en `n_fuentes`/`total` del hero ni en el JSON-LD. Se
  puede sumar después; no es parte de este alcance.
- **Placeholder:** YouTube casi siempre trae miniatura; el `onerror` del `<img>`
  ya degrada al gradiente `.ph`. Un `static/placeholder-divulgacion.png` es
  opcional.
- **Más allá de YouTube:** el tema se llama `divulgacion` (no `youtube`) para
  poder sumar otras fuentes de divulgación (podcasts, etc.) a la misma sección
  sin renombrar.

---

## 12. Estado actual y problemas abiertos (2026-07-01)

Esta sección documenta lo aprendido después de implementar y desplegar
Divulgación. Es la parte más importante para retomar el trabajo.

### 12.1 Qué ya está implementado

- `fetchers.py`: `TOPIC_CONFIG["divulgacion"] = {}` como pass-through.
- `config/sources.yaml`: bloque de canales YouTube con `topics: [divulgacion]`, `category: youtube`, `handle`, `status` y `url` Atom.
- `pipeline.py`: `DEFAULT_FREE_SOURCES` incluye una lista reducida de `yt_*`.
- `web.py`: `_is_divulgacion()`, `_select_divulgacion()`, separación de `divulgacion_raw`, log de conteo y render como tarjetas de video.
- `templates/index.html.j2`: bloque `#divulgacion`, sello `▶`, miniatura y enlace a YouTube.
- `cli.py`: `divulgacion` está en los topics por defecto y excluido del digest temático.
- `locales/*.json`: claves UI de Divulgación y `video_label`.
- `tests/test_divulgacion.py`: tests puros del selector.
- `.github/workflows/regenerate.yml`: el workflow incluye `divulgacion` en `--topics` y falla si `web/index.html` no contiene `id="divulgacion"`.

### 12.2 Evidencia del último workflow

La sección ya aparece en producción, pero el build de GitHub Actions mostró solo
5 tarjetas y muchos errores `yt_*`:

```text
Divulgación: 105 videos recibidos, 5 tarjetas seleccionadas
```

Feeds YouTube que respondieron correctamente en esa corrida de CI:

| Fuente | Resultado CI |
|--------|--------------|
| `yt_ecosdeunmundoestrellado` | `15/15 relevantes` |
| `yt_lagatadeschrodinger` | `15/15 relevantes` |
| `yt_astrumespanol` | `15/15 relevantes` |
| `yt_raqueldelamorenaoficial` | `15/15 relevantes` |
| `yt_darinmex` | `15/15 relevantes` |
| `yt_bitboss` | `15/15 relevantes` |
| `yt_deborahciencia` | `15/15 relevantes` |

Feeds YouTube que fallaron en esa corrida de CI:

| Fuente | Error CI |
|--------|----------|
| `yt_jodisea` | 404 |
| `yt_jefillysh` | 404 |
| `yt_elrobotdeplaton` | 404 |
| `yt_quantumfracture` | 500 |
| `yt_pildorasinformaticas` | 404 |
| `yt_exoplanetas` | 404 |
| `yt_fazttech` | 404 |
| `yt_psicovlog` | 404 |
| `yt_sizematters` | 404 |
| `yt_curiosamente` | 500 |
| `yt_iftmadrid` | 404 |
| `yt_novagea` | 404 |
| `yt_candeliousfang` | 500 |
| `yt_jefidos` | 500 |
| `yt_pontebata` | 500 |
| `yt_cienciadesofa` | 500 |
| `yt_iftwebinars` | 404 |
| `yt_astrovlog` | 404 |
| `yt_javier_garcia` | 500 |

También apareció un fallo no relacionado:

```text
scientific_american [rss] FALLÓ: 403 Client Error: Forbidden
```

Ese `403` no afecta Divulgación; tratarlo por separado si se quiere limpiar el
log general.

### 12.3 Lecciones aprendidas

- La verificación local de feeds YouTube no basta. Varios feeds que respondían en local devolvieron `404` o `500` desde GitHub Actions.
- La fuente de verdad operativa debe ser el comportamiento en CI, porque la web se genera desde GitHub Actions.
- YouTube Atom parece sensible al entorno o a rate/region/infra. Los errores no implican necesariamente que el `channel_id` sea inválido.
- Marcar un canal como `verified_YYYY-MM-DD` por una prueba local es demasiado optimista si luego falla en CI.
- Conviene separar tres estados: `verified_local`, `verified_github`, `feed_unavailable_github`.
- El build ya degrada bien: una fuente `yt_*` que falla no rompe la corrida, pero ensucia logs y reduce tarjetas.
- Con 7 feeds exitosos deberían salir 6 tarjetas. Que salieran 5 sugiere una pérdida posterior al fetch.

### 12.4 Hipótesis de por qué salen 5 tarjetas con 7 feeds exitosos

El pipeline hace:

```python
raw -> dedupe -> cluster_stories -> rank -> diversify -> build_all_sites
```

Divulgación se separa en `build_all_sites`, es decir **después** de `cluster_stories`.
Eso puede ser un problema para videos: `cluster_stories()` agrupa ítems de fuentes
distintas si los títulos se parecen o comparten entidades. En noticias eso es
deseable; en videos curados no. Dos videos de canales distintos pueden compartir
palabras de tema y quedar fusionados como si fueran la misma historia.

Diagnóstico probable:

- Los feeds exitosos aportaron 105 videos crudos.
- Después de dedupe/cluster/rank, `divulgacion_raw` vio menos diversidad efectiva.
- `_select_divulgacion()` solo recibió representantes de clusters, no todos los videos originales.
- Resultado: 5 canales seleccionables aunque 7 feeds habían respondido.

### 12.5 Corrección recomendada para retomar

1. Ajustar `DEFAULT_FREE_SOURCES` a los `yt_*` que funcionaron en GitHub Actions:
   `yt_ecosdeunmundoestrellado`, `yt_lagatadeschrodinger`, `yt_astrumespanol`,
   `yt_raqueldelamorenaoficial`, `yt_darinmex`, `yt_bitboss`, `yt_deborahciencia`.
2. En `config/sources.yaml`, marcar los `yt_*` que fallaron en CI como
   `feed_unavailable_github_2026-07-01` o `verified_local_2026-07-01` según corresponda.
3. Cambiar el pipeline para que los ítems de `divulgacion` no pasen por `cluster_stories`.
4. Mantener dedupe exacto por URL para Divulgación, pero evitar near-dedup por similitud de título.
5. Añadir un test que demuestre que dos videos `divulgacion` de canales distintos con títulos parecidos sobreviven como dos ítems.
6. Añadir un test o comprobación de selector: con 7 canales exitosos sintéticos se seleccionan 6.
7. Ejecutar `python -m pytest tests/ -v`.
8. Lanzar workflow y confirmar en logs: `Divulgación: ... videos recibidos, 6 tarjetas seleccionadas`.

### 12.6 Sketch técnico para excluir Divulgación de clustering

Opción mínima en `pipeline.py`:

```python
deduped = dedupe(raw)
divulgacion = [it for it in deduped if "divulgacion" in it.topics]
clusterable = [it for it in deduped if "divulgacion" not in it.topics]
clustered = cluster_stories(clusterable) + divulgacion
ranked = diversify(rank(clustered))
```

Consideración: si se quiere preservar el conteo del log, cambiarlo a algo como:

```python
log.info(
    "Total: %d crudos -> %d tras deduplicar -> %d tras agrupar historias (+%d divulgación sin cluster)",
    len(raw), len(deduped), len(clustered) - len(divulgacion), len(divulgacion),
)
```

No usar `_is_divulgacion()` desde `web.py` en `pipeline.py` para evitar dependencia circular;
usar directamente `"divulgacion" in it.topics` o mover el predicate a un módulo común si se
vuelve necesario.

### 12.7 Sobre la lista de canales

El Apéndice A y el Apéndice B más abajo son históricos: sirvieron para la primera
implementación, pero ya no deben copiarse literalmente. La lista operativa actual
vive en `config/sources.yaml` y `sibylla/pipeline.py`.

Para próximos cambios, preferir este criterio:

- Documentar todos los canales curados en `sources.yaml`.
- Activar en `DEFAULT_FREE_SOURCES` solo los feeds que funcionen en GitHub Actions.
- Si un canal importante falla en Atom desde CI, buscar alternativa: RSS por proxy propio, API YouTube Data, o scraping controlado. No hacerlo dentro de `fetch_generic_rss` sin aislar costo/riesgo.

---

## Apéndice A — Bloque `sources.yaml` listo para pegar

37 fuentes, una por canal. Pegar dentro de `sources:` en `config/sources.yaml`
(p. ej. en un bloque nuevo tras las fuentes Nacional/Astronomía). `channel_id` y
feed verificados en vivo el 2026-06-30.

```yaml
# =============================================================================
# DIVULGACIÓN CIENTÍFICA — canales de YouTube curados a mano. Cada canal es una
# fuente RSS (feed Atom nativo de YouTube, gratis, sin key). Se muestran como
# tarjetas de video en la sección homónima (1 por canal, 6 más recientes).
# channel_id + feed verificados en vivo el 2026-06-30.
# =============================================================================
  - id: yt_jodisea
    name: "Jodisea | El mundo de las Odiseas"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCz6Cf-gSFs6jDdCo5vctKHA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_radientnews
    name: "Radient News"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCnfI40TiTcj0BNWJkw2y8Mg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_jefillysh
    name: "jefillysh"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCTfQ65AxquXmih3r6rZmK-Q"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_elrobotdeplaton
    name: "El Robot de Platón"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCaVPhFg-Ax873wvhbNitsrQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_jesusgmaestro
    name: "Jesús G. Maestro"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCfWWjBMY6zpvvU6wAe5j_Eg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_quantumfracture
    name: "QuantumFracture"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCbdSYaPD-lr1kW27UJuk8Pw"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_pildorasinformaticas
    name: "pildorasinformaticas"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCdulIs-x_xrRd1ezwJZR9ww"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_exoplanetas
    name: "EXOPLANETAS Noticias Ciencia y Tecnología"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC92-DgDvLQPM_r2oHbDr60w"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_robotitus
    name: "Noticias Robotitus"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC5rJaxmE3UyVz59OGxbPdAg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_ecosdeunmundoestrellado
    name: "Ecosdeunmundoestrellado"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCSoZXagwqt4OYBXjAYKbjpQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_fazttech
    name: "Fazt"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCX9NJ471o7Wie1DQe94RVIg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_psicovlog
    name: "Psico Vlog"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCKK25Prf-UD9Qak0W1d02KQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_lagatadeschrodinger
    name: "La gata de Schrödinger"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCoXtmmnLCbXDiSo8GxsmOzA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_sizematters
    name: "SizeMatters"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC6h-HID9dV2BAGSMy4_J84g"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_curiosamente
    name: "CuriosaMente"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCX16cLWl6dCjlZMgUBxgGkA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_iftmadrid
    name: "Instituto de Física Teórica IFT"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCk195x4zYdMx4LhqEwhcPng"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_novagea
    name: "NovaGea"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCpRzJ0uX8ATOQkJfBfGS1uA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_astrumespanol
    name: "Astrum Español"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC-2A6Z4k5-AJBLO_wDA-ZUg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_raqueldelamorenaoficial
    name: "Raquel de la Morena"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC_0j0mtBAGTj1U7LD3J0dhQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_ter
    name: "Ter"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCCNgRIfWQKZyPkNvHEzPh7Q"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_darinmex
    name: "Darin McNabb"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC6GbAKHWYUJDWlkxY6HPldg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_candeliousfang
    name: "Candeliousfang"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCJAsPjHqO0AHSd6Uasj7oVA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_jefidos
    name: "Carolina Jefillysh"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCnN7Hrbc7dsvuDVkU6ro-SQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_matesmike
    name: "Mates Mike"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC-_kZ3UZBsnCWJEVr8ysMww"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_pontebata
    name: "Ponte Bata"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCmwwKS5em7UNZvghNcfugHg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_cienciadesofa
    name: "CienciaDeSofa"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCMbQbVilo-nezMvwf1BZfAA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_bitboss
    name: "BitBoss"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC51m1mQmjKJ10YZWtRr8tgA"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_cinematixfilms
    name: "Cinematix"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCpuKDBw8IVIdKWPhiB2VDNQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_anatomiahumanaydiseccion
    name: "Anatomía Humana y Disección"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCw-oldhkk_2ftVa_PL0eoSQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_iftwebinars
    name: "IFT Webinars"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCcdmQRtxk_xdpt9iFATTDQQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_astrovlog
    name: "astrovlog"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC-8DZ-sTOoohhzndLNl4w1A"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_lahiperactina
    name: "La Hiperactina"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCV5G678sZwW5IcF3pCfRbHQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_alvamajo
    name: "Alva Majo"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCmaEoq1zaakpdudbzgll-zw"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_javier_garcia
    name: "Javier Garcia"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCYOv9HwOFwK0lY2dUQlZSpg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_antroporamadivulgacion
    name: "Antroporama"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCGKzjVZGdJ0YmUqg42xfO5w"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_deborahciencia
    name: "deborahciencia"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UCibUX4QoSrRwmBZf0Ig-OCg"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30

  - id: yt_midudev
    name: "midudev"
    publisher: "YouTube"
    tier: 3
    type: rss
    category: youtube
    url: "https://www.youtube.com/feeds/videos.xml?channel_id=UC8LeXCWOalN8SxlrPcG-PaQ"
    topics: [divulgacion]
    lang: es
    license: "solo miniatura + título + enlace al video"
    access: open
    cost: free
    status: verified_2026-06-30
```

---

## Apéndice B — Ids para `DEFAULT_FREE_SOURCES`

Pegar en `sibylla/pipeline.py` dentro de la lista `DEFAULT_FREE_SOURCES`
(en su propio bloque comentado):

```python
    # Divulgación científica: 37 canales de YouTube (1 tarjeta por canal)
    "yt_jodisea", "yt_radientnews", "yt_jefillysh", "yt_elrobotdeplaton", "yt_jesusgmaestro", "yt_quantumfracture",
    "yt_pildorasinformaticas", "yt_exoplanetas", "yt_robotitus", "yt_ecosdeunmundoestrellado", "yt_fazttech", "yt_psicovlog",
    "yt_lagatadeschrodinger", "yt_sizematters", "yt_curiosamente", "yt_iftmadrid", "yt_novagea", "yt_astrumespanol",
    "yt_raqueldelamorenaoficial", "yt_ter", "yt_darinmex", "yt_candeliousfang", "yt_jefidos",
    "yt_matesmike", "yt_pontebata", "yt_cienciadesofa", "yt_bitboss", "yt_cinematixfilms", "yt_anatomiahumanaydiseccion",
    "yt_iftwebinars", "yt_astrovlog", "yt_lahiperactina", "yt_alvamajo", "yt_javier_garcia", "yt_antroporamadivulgacion",
    "yt_deborahciencia", "yt_midudev",
```

> ⚠️ **Rendimiento del build:** 37 fuentes RSS nuevas = 37 GET extra por corrida.
> Cada feed es ligero (~50 KB) y el fallo de una fuente está aislado, pero el
> tiempo de fetch crecerá. Si se vuelve un problema, considerar paralelizar el
> fetch de las fuentes `category: youtube` (hoy el pipeline las baja en serie).
