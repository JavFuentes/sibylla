# PLAN-SOCIAL2.md — Fase social 2: orden social de tarjetas + comentarios + anti-abuso

> Documento de implementación para agentes. Ejecutar los pasos en orden; cada paso deja
> el repo en verde (tests + build) antes de pasar al siguiente. El autor revisa al final.
> Comentarios y docs en **español** (convención del repo). **Nunca** subir `.env` ni
> imprimir claves; la `firebaseConfig` NO es secreto y va commiteada (ver PLAN-SOCIAL.md).
> Leer AGENTS.md y PLAN-SOCIAL.md antes de empezar: este plan asume la Fase 1 ya en main
> (commit `603a47d`).

## Contexto y estado actual

La Fase 1 dejó implementado: botones like/dislike/comentarios por tarjeta con *reading
gate*, login Firebase (Google popup + email/contraseña), votos en `votes` (doc id
`<cardId>_<uid>`), contadores por aggregation lazy (IntersectionObserver + sessionStorage
TTL 30 min) y reglas en `firestore.rules`. La auth ya funciona en producción.

**Problemas conocidos que esta fase corrige:**
1. **Los contadores de likes no se ven.** Causas candidatas (diagnosticar en Paso 0):
   índice compuesto `votes(card, value)` ausente (la aggregation falla con
   `failed-precondition` y `social.js` lo silencia con `console.warn`), reglas sin
   publicar en la consola, o simplemente que `pintarConteo` deja el número vacío
   cuando vale 0 (`static/social.js` L124-125).
2. **El chip de sesión del header se ve mal** (`#sesion-chip`: botón 30 px con la
   inicial; en móvil va con `position:absolute; right:54px`). Se rediseña en Paso 7.
3. El botón de comentarios es un teaser («próximamente»). Se implementa de verdad.

**Qué añade la Fase 2:** contadores siempre visibles, orden de tarjetas por interacción
(horneado en el build + reordenado en vivo al cargar, con cortina de carga), comentarios
con reporte y auto-ocultado, hardening anti-abuso (reglas, App Check, CSP) y una única
Cloud Function (el proyecto ya está en Blaze) que mantiene los conteos pre-agregados en
`agregados/conteos` — 1 read por visitante en vez de ~2 por tarjeta.

## Decisiones ya tomadas por el autor (no re-litigar)

- **Orden de tarjetas — dónde:** el build (~11:00 Chile, cron `regenerate.yml` 14:08 UTC)
  hornea el **orden inicial** leyendo los conteos de Firestore; al cargar la página,
  `social.js` pide conteos frescos y **reordena en vivo** si el orden cambió, cubriendo
  el intercambio con una **cortina de carga** (nada de tarjetas saltando a la vista).
- **Orden de tarjetas — fórmula:** `puntaje = (likes − dislikes) + 2 × comentarios_visibles`.
  Orden descendente **estable**: a igual puntaje manda el orden editorial actual. Con eso
  las más gustadas suben y las más rechazadas caen a la última posición de su sección.
- **La selección sigue siendo editorial.** Los votos ordenan las 6 tarjetas elegidas de
  cada sección; **jamás** deciden qué entra o sale de la portada (anti-brigading).
- **Plan Blaze habilitado (2026-07-04), con filosofía free-tier:** el proyecto NO debe
  generar gasto salvo que escale en serio. De Blaze se usa **una sola Cloud Function**
  (mantener los conteos pre-agregados en `agregados/conteos`, Paso 4): reduce el costo
  de lectura de ~120 reads/visitante frío a **1 read/visitante** y cabe holgadísima en
  la capa gratuita de Functions (2M invocaciones/mes). Nada más se mueve a Functions:
  moderación, reportes y rate-limit siguen en reglas (menos piezas desplegadas, sin
  latencia de backend en la UX). Las **alertas de presupuesto** en GCP son obligatorias
  (Paso 3): en Blaze ya no hay tope duro de gasto.
- **Moderación:** comentar exige **correo verificado** (Google llega verificado; a los de
  email se les envía verificación). Un comentario se **auto-oculta al juntar 3 reportes
  de usuarios distintos** — implementado 100 % con reglas de Firestore (decisión
  deliberada: la transacción del reporte es atómica y no depende de ningún deploy de
  backend). Lo oculto queda en la colección para revisión manual en consola.
- **Votar NO exige verificación** (se mantiene el comportamiento de Fase 1).
- **Avatar del header:** foto de la cuenta (`photoURL`, Google) recortada en círculo con
  anillo dorado; si no hay foto (email), inicial en Cinzel dorado. Popover con nombre,
  correo y cerrar sesión.
- **Comentarios:** hilo plano, más reciente primero, 20 por página («ver más»), máximo
  500 caracteres, texto plano (render con `textContent`, sin auto-links, sin HTML).
  Borrar el propio comentario: sí. Editar: no (fuera de alcance).
- **Identidad del comentario:** el nombre público es `displayName`. Los usuarios de email
  sin `displayName` lo fijan una única vez antes del primer comentario (mini-campo en el
  panel, `updateProfile` + refresh del token). Las reglas exigen
  `autor == request.auth.token.name` (nadie firma con nombre ajeno).
- **Reportes:** 1 por usuario por comentario (doc id `<commentId>_<uid>`), irreversibles,
  exigen correo verificado, no se puede reportar el comentario propio.

## Archivos afectados

- `functions/` (**nuevo**) + `firebase.json` + `.firebaserc` — Cloud Function de conteos
  (Node, `firebase-functions` v2). Se despliega a mano con la CLI de Firebase (no desde
  los workflows). Sin secretos: el project id es público.
- `sibylla/social_sync.py` (**nuevo**) — lectura del doc de conteos vía Firestore REST
  en el build.
- `sibylla/web.py` — orden social en `build_context` (L1006-1121: `grupos`, `astro_cards`,
  `divulgacion_cards`, `sibylla_cards`, `social_cards`) + `social_conteos` al contexto.
- `sibylla/templates/index.html.j2` — JSON `#social-conteos`, CSS (cortina, comentarios,
  chip), popover de sesión, textos nuevos en `#social-i18n`.
- `static/social.js` — contadores instantáneos, reordenado en vivo + cortina, panel de
  comentarios completo, verificación de correo, apodo, avatar.
- `firestore.rules` — v2: `comments`, `reports`, `users` (rate-limit), `votes` intacto.
- `locales/es.json` — claves nuevas (solo `es`, como en Fase 1).
- `static/.htaccess` — CSP + `X-Content-Type-Options`.
- `tests/` — tests nuevos de `social_sync` y del orden social.
- No tocar: el `<script>` ES5 del template (solo leerlo para integrarse), `i18n.py`,
  resolver, workflows (salvo nada: el build no necesita secretos nuevos en esta fase).

---

## Paso 0 — Diagnóstico y cierre de pendientes de Fase 1

**0a. Consola Firebase (manual, con el autor si hace falta):**
1. Firestore → Reglas: confirmar que el contenido publicado == `firestore.rules` del repo.
2. Authentication → Settings → Authorized domains: `sibylla.cl`, `www.sibylla.cl`,
   `localhost` presentes.
3. Abrir `https://sibylla.cl` con DevTools: si la consola muestra
   `failed-precondition` en los conteos, seguir el **enlace del error** para crear el
   índice compuesto `votes(card ASC, value ASC)` con un clic. Ese es el fix más probable
   de «no se ven los likes».
4. Authentication → Settings: verificar que la **protección contra enumeración de
   correos** está activada.

**0b. Contadores visibles siempre.** En `static/social.js`, `pintarConteo` (L116-126):
pintar el número **siempre que haya dato, incluido 0** (hoy `likes > 0 ? … : ''`). Con el
JSON horneado del Paso 4 todas las tarjetas tendrán dato desde el primer pintado.

## Paso 1 — Claves de locales (solo `locales/es.json`, sección `web`)

Añadir (nombres exactos; valores ES sugeridos, ajustar tono sibilino si se quiere):
- `social_orden_cargando` «Ordenando el ágora…» (cortina de carga)
- `social_comments_title` «Comentarios», `social_comment_placeholder` «Escribe tu
  comentario…», `social_comment_send` «Publicar», `social_comment_empty` «Sé la primera
  voz de esta conversación.», `social_comment_more` «Ver más», `social_comment_count_aria`
  «{n} comentarios»
- `social_comment_delete` «Eliminar», `social_comment_delete_confirm` «¿Eliminar tu
  comentario?», `social_comment_report` «Reportar», `social_comment_report_confirm`
  «¿Reportar este comentario como inapropiado?», `social_comment_reported` «Gracias. Tu
  reporte quedó registrado.», `social_comment_error` «No se pudo publicar. Intenta de
  nuevo.», `social_comment_rate` «Espera unos segundos antes de comentar de nuevo.»
- `social_verify_needed` «Verifica tu correo para comentar.», `social_verify_sent` «Te
  enviamos un correo de verificación. Revísalo y recarga la página.»,
  `social_verify_resend` «Reenviar verificación»
- `social_nick_label` «Nombre público», `social_nick_hint` «Así te verán en los
  comentarios (2–40 caracteres).», `social_nick_save` «Guardar», `social_nick_error`
  «Ese nombre no es válido.»

Recordar: `en/it/pt.json` NO se tocan (paridad ya recortada en Fase 1). Exponer todas las
claves nuevas en el JSON `#social-i18n` del template.

## Paso 2 — Modelo de datos y `firestore.rules` v2

Colecciones nuevas (las de Fase 1 no cambian):

- **`comments/{autoId}`**: `{card, uid, autor, texto, ts, reportes: int, oculto: bool}`.
  `card` = mismo cardId de `votes`. `autor` = snapshot del `displayName`. `reportes`
  nace en 0, `oculto` en false; **solo** el flujo de reporte puede tocarlos.
- **`reports/{commentId}_{uid}`**: `{comment, uid, ts}`. Ilegibles para clientes.
- **`users/{uid}`**: `{lastCommentAt?, lastReportAt?}` — solo para rate-limit por reglas.
- **`agregados/conteos`** (doc único): `{<cardId>: {l, d, c}, ...}` — conteos por tarjeta
  mantenidos por la Cloud Function (Paso 4). Lectura pública; **nadie** escribe por
  cliente (la función usa Admin SDK, que salta las reglas).

Reemplazar `firestore.rules` por esta v2 (el bloque `votes` queda **idéntico** al actual;
el catch-all deny sigue al final):

```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {

    // ---- votes: sin cambios respecto de Fase 1 (copiar el bloque actual) ----

    // Comentarios. Lectura pública SOLO de visibles: toda query de lista debe
    // filtrar oculto == false (las reglas lo fuerzan). El texto es plano; el
    // cliente lo renderiza con textContent (nunca innerHTML).
    match /comments/{commentId} {
      allow read: if resource.data.oculto == false;

      // Crear: sesión + correo verificado + campos válidos + autor == token.name
      // + rate-limit 1 comentario/30 s vía users/{uid} (batch obligatorio: el
      // cliente escribe el comentario y users/{uid}.lastCommentAt en el mismo
      // writeBatch; getAfter lo verifica).
      allow create: if request.auth != null
        && request.auth.token.email_verified == true
        && request.resource.data.keys().hasOnly(['card','uid','autor','texto','ts','reportes','oculto'])
        && request.resource.data.uid == request.auth.uid
        && request.resource.data.card is string
        && request.resource.data.card.size() >= 3
        && request.resource.data.card.size() <= 64
        && request.resource.data.texto is string
        && request.resource.data.texto.size() >= 2
        && request.resource.data.texto.size() <= 500
        && request.resource.data.autor == request.auth.token.name
        && request.resource.data.autor.size() >= 2
        && request.resource.data.autor.size() <= 40
        && request.resource.data.reportes == 0
        && request.resource.data.oculto == false
        && request.resource.data.ts == request.time
        && getAfter(/databases/$(database)/documents/users/$(request.auth.uid)).data.lastCommentAt == request.time
        && (!exists(/databases/$(database)/documents/users/$(request.auth.uid))
            || !('lastCommentAt' in get(/databases/$(database)/documents/users/$(request.auth.uid)).data)
            || get(/databases/$(database)/documents/users/$(request.auth.uid)).data.lastCommentAt
               < request.time - duration.value(30, 's'));

      // Borrar: solo el autor.
      allow delete: if request.auth != null && resource.data.uid == request.auth.uid;

      // Update = SOLO reportar: autenticado y verificado, no el autor, cambia
      // únicamente reportes (+1 exacto) y oculto (umbral 3), y en la MISMA
      // transacción nace reports/<commentId>_<uid> (existsAfter) que antes no
      // existía (un reporte por usuario, para siempre).
      allow update: if request.auth != null
        && request.auth.token.email_verified == true
        && request.auth.uid != resource.data.uid
        && request.resource.data.diff(resource.data).affectedKeys().hasOnly(['reportes','oculto'])
        && request.resource.data.reportes == resource.data.reportes + 1
        && request.resource.data.oculto == (request.resource.data.reportes >= 3 || resource.data.oculto)
        && !exists(/databases/$(database)/documents/reports/$(commentId + '_' + request.auth.uid))
        && existsAfter(/databases/$(database)/documents/reports/$(commentId + '_' + request.auth.uid));
    }

    // Reportes: solo creación, atada al incremento del comentario (misma
    // transacción) y con rate-limit 1 reporte/10 s vía users/{uid}.
    match /reports/{reportId} {
      allow read: if false;
      allow create: if request.auth != null
        && request.auth.token.email_verified == true
        && request.resource.data.keys().hasOnly(['comment','uid','ts'])
        && request.resource.data.uid == request.auth.uid
        && reportId == request.resource.data.comment + '_' + request.auth.uid
        && request.resource.data.ts == request.time
        && getAfter(/databases/$(database)/documents/users/$(request.auth.uid)).data.lastReportAt == request.time
        && (!exists(/databases/$(database)/documents/users/$(request.auth.uid))
            || !('lastReportAt' in get(/databases/$(database)/documents/users/$(request.auth.uid)).data)
            || get(/databases/$(database)/documents/users/$(request.auth.uid)).data.lastReportAt
               < request.time - duration.value(10, 's'));
    }

    // users/{uid}: marcas de tiempo para rate-limit. Solo el dueño escribe y
    // cada campo presente debe valer request.time.
    match /users/{userId} {
      allow read: if request.auth != null && request.auth.uid == userId;
      allow create: if request.auth != null && request.auth.uid == userId
        && request.resource.data.keys().hasOnly(['lastCommentAt','lastReportAt'])
        && (!('lastCommentAt' in request.resource.data) || request.resource.data.lastCommentAt == request.time)
        && (!('lastReportAt' in request.resource.data) || request.resource.data.lastReportAt == request.time);
      allow update: if request.auth != null && request.auth.uid == userId
        && request.resource.data.keys().hasOnly(['lastCommentAt','lastReportAt'])
        && (!('lastCommentAt' in request.resource.data.diff(resource.data).affectedKeys())
            || request.resource.data.lastCommentAt == request.time)
        && (!('lastReportAt' in request.resource.data.diff(resource.data).affectedKeys())
            || request.resource.data.lastReportAt == request.time);
    }

    // Conteos pre-agregados: los mantiene la Cloud Function (Admin SDK,
    // ignora estas reglas). Ningún cliente puede escribirlos.
    match /agregados/{docId} {
      allow read: if true;
      allow write: if false;
    }

    match /{document=**} { allow read, write: if false; }
  }
}
```

**Validar la sintaxis y la semántica ANTES de publicar** con el emulador
(`firebase emulators:start --only firestore` + `@firebase/rules-unit-testing`; ver
Paso 9). Ojo con dos sutilezas: (a) las comparaciones con `request.time` exigen que el
cliente use `serverTimestamp()`; (b) `getAfter`/`exists` cuentan para el tope de 10
accesos a documentos por evaluación — este diseño usa ≤3.

## Paso 3 — Consola Firebase (manual)

1. Publicar las reglas v2 (copiar `firestore.rules` → Reglas → Publicar).
2. Authentication → Templates: poner en **español** la plantilla del correo de
   verificación (y confirmar la de reset de Fase 1).
3. Índices: **no crear a mano**. La primera query de comentarios
   (`card == X && oculto == false orderBy ts desc`) fallará con `failed-precondition`
   y el enlace del error crea `comments(card ASC, oculto ASC, ts DESC)` con un clic.
   Si las aggregations de la Cloud Function piden otro índice, el enlace aparece en los
   **logs de la función** (consola → Functions → Registros).
4. **App Check** (anti-abuso de API): App Check → registrar la app web con
   **reCAPTCHA v3** (crear la clave del sitio para `sibylla.cl`/`www.sibylla.cl`) y
   dejarlo en **modo monitor** (sin *enforcement*). El enforcement se activa en una
   fase posterior (ver Apéndice): requiere primero migrar las lecturas REST del build
   a un service account, porque el enforcement bloquea el REST anónimo.
5. **Alertas de presupuesto (OBLIGATORIO con Blaze):** Google Cloud Console →
   Facturación → Presupuestos y alertas → crear presupuesto de **1 USD** con avisos al
   50/90/100 % (y otro de 5 USD como red de emergencia). En Blaze no existe tope duro:
   sin alertas, un abuso de lecturas pasa de "la capa social degrada" (Spark) a "llega
   una factura". El objetivo del proyecto sigue siendo gastar $0.
6. Instalar/loguear la **CLI de Firebase** (`npm i -g firebase-tools`,
   `firebase login`) para el deploy de la función y el emulador de reglas.

## Paso 4 — Conteos agregados: Cloud Function + build

**4a. Cloud Function `functions/` (nueva; única pieza que usa Blaze).** Node 20+ con
`firebase-functions` v2 y `firebase-admin`. Dos triggers:
- `onDocumentWritten('votes/{voteId}')`
- `onDocumentWritten('comments/{commentId}')`

Ambos hacen lo mismo: extraen el `card` del doc escrito/borrado (del `after` o, si fue
borrado, del `before`), **recalculan** los conteos de ESA tarjeta con aggregation
queries del Admin SDK (votos: `count` + `sum('value')`; comentarios: `count` con
`oculto == false`) y escriben el resultado en `agregados/conteos` con
`set({ [cardId]: {l, d, c} }, { merge: true })`.

Diseño **recalcular-en-vez-de-incrementar**, a propósito: los triggers de Functions son
*at-least-once* (un incremento se puede aplicar dos veces y el contador deriva); el
recálculo es idempotente y se auto-corrige en la siguiente escritura. Costo por
voto/comentario: 1-2 reads de aggregation + 1 write — despreciable frente a las cuotas
gratis (2M invocaciones/mes de Functions; los reads/writes de Firestore de la función
cuentan en la cuota diaria normal).

- `firebase.json` + `.firebaserc` (project `sibylla-a81d2`) en la raíz; el código en
  `functions/` con su `package.json`. Comentarios en español. **Sin secretos.**
- Deploy manual: `firebase deploy --only functions` (el autor; NO cablearlo a los
  workflows de CI en esta fase).
- **Seed inicial (una vez):** los votos de Fase 1 ya existentes no tienen entrada en
  `agregados/conteos` hasta que alguien vuelva a votar esa tarjeta. Añadir
  `functions/seed.js` (script local con `firebase-admin` + Application Default
  Credentials via `gcloud auth application-default login`): recorre los `card` distintos
  de `votes`/`comments` y puebla el doc con la misma lógica. Correrlo una vez tras el
  primer deploy; documentar el comando en el README de `functions/`.

**4b. `sibylla/social_sync.py` (nuevo).** En el build, **una sola llamada REST** (sin
secretos: el doc es de lectura pública y basta la `apiKey` pública):
`GET https://firestore.googleapis.com/v1/projects/sibylla-a81d2/databases/(default)/documents/agregados/conteos?key=<apiKey>`
→ parsear los `fields` del doc a `{cardId: {"l": int, "d": int, "c": int}}` (ojo con el
formato de valores de la REST API: `integerValue` llega como string). API:
`fetch_conteos(api_key: str, project_id: str) -> dict[str, dict]`, timeout 10 s y
**fallo aislado** (convención del repo): cualquier excepción o doc inexistente ⇒
`log.warning` + `{}` (orden editorial, sitio intacto). La `apiKey`/`projectId` van como
constantes en el código (son las públicas del template, NO a `.env`).

**4c. Orden social en `sibylla/web.py`.** En `build_context` (L1006-1121), tras armar
`grupos`, `astro_cards`, `divulgacion_cards`, `sibylla_cards` y `social_cards`:

```python
conteos = fetch_conteos(api_key=..., project_id=...)
def _puntaje(c):  # dict de _tarjeta
    v = conteos.get(c["id"], {})
    return (v.get("l", 0) - v.get("d", 0)) + 2 * v.get("c", 0)
# sort ESTABLE descendente dentro de cada sección; sin conteos, no-op:
for g in grupos: g["cards"].sort(key=_puntaje, reverse=True)
# ídem astro_cards / divulgacion_cards / sibylla_cards / social_cards
```

`sorted`/`list.sort` de Python son estables: con `{}` el orden editorial queda intacto.
Añadir `social_conteos = conteos` al contexto del template. Respetar la firma/flujo de
`build_context` (no romper `render_html` L1125-1140 ni los tests existentes).

**4d. Template.** Junto al `#social-i18n` (L1650 aprox), añadir:
`<script type="application/json" id="social-conteos">{{ social_conteos | tojson }}</script>`
(dict posiblemente vacío; el HTML es `no-cache`, así que se refresca con cada deploy).

## Paso 5 — Cliente: contadores instantáneos + reorden en vivo con cortina

Cambios en `static/social.js` (mantener la filosofía: si Firebase no carga, TODO esto se
queda quieto y el sitio es el estático):

**5a. Pintado instantáneo.** Al arrancar (antes incluso del init de Firebase no hace
falta: sigue dentro del módulo), leer `#social-conteos` y pintar todos los `.soc-num`
(incluido 0) y el contador de comentarios del botón `.soc-com`. Cero reads.

**5b. Conteos frescos.** Sustituir el IntersectionObserver y las aggregations por
tarjeta por **un único `getDoc` de `agregados/conteos`** al inicio (lo mantiene la
Cloud Function del Paso 4). Mantener la caché sessionStorage TTL 30 min (si está
fresca, usarla y NO pedir red). Coste frío: **1 read por visitante**; recargas <30 min,
0. Las tarjetas sin entrada en el doc (sin interacción aún) valen `{l:0,d:0,c:0}`.
Si el `getDoc` falla, se queda el horneado del 5a y no hay reorden (degradación).

**5c. Reorden en vivo.** Cuando una sección tiene sus conteos frescos completos:
1. Calcular el orden con la MISMA fórmula del build (`l−d+2c`, sort estable por orden
   DOM actual como desempate).
2. Si el orden resultante == orden del DOM → no hacer nada (caso común recién
   desplegado: el build ya venía ordenado).
3. Si difiere → mostrar la **cortina** de esa zona, reordenar los nodos `.carta` dentro
   del contenedor de tarjetas de la sección, y retirar la cortina (fade). Duración
   mínima 300 ms para que no parpadee; **tope global 2000 ms**: pasado el tope se retira
   la cortina pase lo que pase y NO se reordena después (nunca mover tarjetas ante los
   ojos del usuario; los conteos sí pueden seguir refrescándose).
4. El reorden ocurre **una sola vez por carga de página**.

**Cortina:** overlay `position:absolute` sobre `#secciones` (o por sección, a criterio
del implementador) con fondo `rgba(8,11,20,.72)` + `backdrop-filter:blur(6px)`, un glifo
animado sutil acorde a la estética (p. ej. la estrella de 8 puntas pulsando en dorado) y
`{{ t.social_orden_cargando }}` en Cormorant itálica. Respetar
`prefers-reduced-motion` (sin animación, solo el velo). La cortina la **crea el JS**
(nunca está en el HTML estático: si el módulo no carga, no hay velo). CSS en el bloque
social del template. NO reutilizar `#secciones.is-reordering` (es del drag de secciones
del ES5); usar clase nueva, p. ej. `.social-velo`.

**Integración con el script ES5 (leer L967-1505 del template antes de tocar):**
- **Modo aleatorio:** si el usuario está en modo feed (el ES5 mueve las tarjetas a
  `#feed`), **no reordenar** (detectar el modo igual que el ES5: misma clave de
  localStorage / mismo estado del DOM).
- **Selector de tarjetas (`sibylla_cards`):** el ES5 limita cuántas tarjetas se ven por
  sección. Tras reordenar, re-aplicar la visibilidad **por posición**: contar cuántas
  estaban visibles y dejar visibles las primeras N del nuevo orden (así "top votadas
  primero" respeta la preferencia de cantidad del usuario).
- No tocar el ES5; toda la lógica va en `social.js`.

## Paso 6 — Cliente: comentarios completos

Reemplazar el teaser (`social.js` L240-256) por el sistema real. El panel
`.comentarios-panel` cuelga de la `.carta` (estilos ya esbozados en Fase 1: props de
`.resumen-panel` con tinte cian).

**6a. Abrir/pintar.** Clic en `.soc-com` → toggle del panel. Primera apertura: query
`comments` `where card == id && oculto == false, orderBy ts desc, limit 20` → render.
Botón «Ver más» pagina con `startAfter` (siguiente 20). Cada ítem: autor (Cinzel,
dorado suave), fecha relativa, texto, y acciones: **Eliminar** (solo propios, con
confirm) / **Reportar** (solo ajenos). **Render exclusivamente con
`createElement`/`textContent`** — prohibido `innerHTML` con datos de Firestore (UGC).

**6b. Formulario.** Al fondo del panel, solo con sesión:
- Sin sesión → el clic en `.soc-com` sigue abriendo `abrirAuth('comment')` (como hoy).
- Con sesión sin `emailVerified` → en vez del form, mensaje `social_verify_needed` +
  botón `social_verify_resend` (`sendEmailVerification`). Además, al **registrarse** por
  email (flujo del modal de Fase 1) disparar `sendEmailVerification` automáticamente y
  mostrar `social_verify_sent`. Tras verificar, hace falta `getIdToken(true)` o recarga.
- Con sesión verificada y sin `displayName` (usuarios email) → mini-campo
  `social_nick_label` (2–40 chars, sin URLs — validación cliente) → `updateProfile` +
  `getIdToken(true)` → recién entonces mostrar el textarea.
- Publicar: textarea `maxlength=500` + contador de caracteres; `writeBatch`:
  `addDoc`-style ref con id auto en `comments` + `set` de
  `users/{uid}.lastCommentAt = serverTimestamp()` (merge). Optimista: insertar el
  comentario arriba al confirmar; error `permission-denied` dentro de los 30 s del
  rate-limit → toast `social_comment_rate`; otros → `social_comment_error`.
- Actualizar el contador del botón `.soc-com` (+1) y la caché de conteos (el reorden NO
  se re-dispara: regla del Paso 5c.4).

**6c. Borrar propio.** `deleteDoc` + quitar el nodo + contador −1.

**6d. Reportar.** Confirm (`social_comment_report_confirm`) → `runTransaction`:
1. `get` del comentario (para `reportes` exacto);
2. `update` del comentario: `reportes = n+1`, `oculto = (n+1 >= 3)`;
3. `set` de `reports/<commentId>_<uid>` `{comment, uid, ts: serverTimestamp()}`;
4. `set` de `users/{uid}.lastReportAt = serverTimestamp()` (merge).
Al confirmar: toast `social_comment_reported`, ocultar ese comentario **localmente**
para el reportante (y si quedó `oculto`, desaparece para todos en la próxima carga).
Error por reporte duplicado (`permission-denied`) → tratar como "ya reportado"
silencioso.

**6e. A11y.** El panel con `role="region"` + `aria-label`; `aria-expanded` en `.soc-com`
(ya existe); foco al textarea al abrir con teclado; toasts con `role="status"`.

## Paso 7 — Avatar y sesión del header

Rediseñar `#sesion-chip` y `#sesion-menu` (template L441-462, L706, L731-738). **Invocar
la skill `frontend-design`** para este paso y verificar con screenshots (build local +
`python -m http.server 8000 --directory web`).

- **Chip:** 32 px, círculo perfecto, **anillo dorado** (borde 1px `rgba(217,184,95,.55)`
  + glow sutil al hover, mismo lenguaje que `.sec-btn`). Si `user.photoURL` existe →
  `<img>` dentro del botón (`referrerpolicy="no-referrer"`, `alt=""`,
  `border-radius:50%; object-fit:cover`) con `onerror` que cae a la inicial; si no →
  inicial en Cinzel dorado sobre fondo `#0B0F1C`. El JS decide foto/inicial en
  `pintarSesion` (`social.js` L387-404).
- **Alineación:** el chip debe quedar **ópticamente centrado** con la hamburguesa y la
  marca en desktop y móvil. Revisar el hack móvil actual
  (`.sesion{position:absolute; right:54px; top:50%; transform:translateY(-50%)}`, L706):
  comprobar en 375/480 px que no colisiona con la hamburguesa ni con la marca; ajustar
  lo que haga falta (gap, tamaño del chip en móvil, z-index).
- **Popover (`#sesion-menu`):** añadir el nombre (`displayName`) sobre el correo,
  esquinas y borde coherentes con `.onb-panel`, flecha/caret opcional, sombra suave.
  Mantener el toggle por clic y el cierre por clic-fuera ya implementados.
- **Botón «Entrar»:** revisar tipografía/espaciado (hoy `.sesion-entrar` es texto plano
  tracking-ado; que respire igual que los enlaces del nav).

## Paso 8 — Anti-abuso y hardening (además de las reglas del Paso 2)

1. **App Check (reCAPTCHA v3) en el cliente:** inicializarlo en `social.js` justo tras
   `initializeApp` (import dinámico de `firebase-app-check.js` en el mismo try/catch;
   la clave del sitio reCAPTCHA viaja en `#social-i18n`, es pública). En modo monitor no
   bloquea nada; deja métricas para decidir el enforcement (Apéndice).
2. **CSP en `static/.htaccess`.** Añadir dentro de `<IfModule mod_headers.c>`:
   - `Header set X-Content-Type-Options "nosniff"`
   - `Header set Content-Security-Policy` con, como mínimo:
     `default-src 'self'; script-src 'self' 'unsafe-inline' https://www.gstatic.com https://apis.google.com; connect-src 'self' https://*.googleapis.com https://www.google.com; img-src 'self' data: https:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; frame-src https://sibylla-a81d2.firebaseapp.com https://accounts.google.com https://www.google.com; object-src 'none'; base-uri 'self'; form-action 'self'`
   - `'unsafe-inline'` es obligado hoy (script ES5 + JSON inline); la CSP igual corta
     la exfiltración (connect-src) y los iframes ajenos. Ajustar contra la realidad:
     probar login Google, Firestore, reCAPTCHA, imágenes de tarjetas y fuentes; revisar
     violaciones en la consola del navegador ANTES de desplegar. Si el popup de Google
     rompe en producción, retirar el header (deploy) y reintentar con los dominios que
     falten.
3. **Presupuesto de lecturas.** Ojo: en Blaze, agotar la cuota gratis diaria (50K
   reads) ya **no degrada — factura**. Mitigado por: 1 read/visitante (doc de conteos),
   caché de sesión 30 min, conteos horneados, App Check y las alertas del Paso 3.
   Vigilar en la consola (Usage) la primera semana tras el deploy; el sitio estático
   nunca depende de nada de esto.
4. **Privacidad / Ley 21.719 (vigencia 1-dic-2026):** los comentarios llevan nombre
   público — dato personal. Esta fase entrega: borrado del comentario propio y mínimo
   dato necesario (`users/{uid}` solo guarda timestamps). Queda ANOTADO como pendiente
   de Fase 3: eliminación de cuenta completa (auth + votos + comentarios) y política de
   privacidad publicada en el sitio.
5. **Nombres de usuario:** las reglas atan `autor` al `token.name`, y el nick se valida
   en cliente (sin URLs). Si aparece abuso de nicks (suplantación de medios, etc.), la
   revisión manual en consola puede borrar el comentario; endurecer con lista de nombres
   reservados queda como opción futura.

## Paso 9 — Tests

1. **Python:** tests nuevos `tests/test_social_sync.py` — `fetch_conteos` con
   `requests` mockeado (éxito, timeout, respuesta malformada ⇒ `{}` y warning) — y del
   orden social en `build_context` (con conteos inyectados: la tarjeta más votada
   primero, la más rechazada última, empate conserva orden editorial, sin conteos
   ⇒ orden intacto). Correr la suite completa (`pytest`), debe seguir en verde.
2. **JS:** `node --check static/social.js` y `node --check functions/index.js`
   (convención de Fase 1). La función además se prueba en el emulador
   (`firebase emulators:start --only functions,firestore` + escribir un voto y ver el
   doc `agregados/conteos` actualizarse) antes del deploy real.
3. **Reglas (muy recomendado):** mini-proyecto `tools/rules-tests/` (npm,
   `@firebase/rules-unit-testing` + emulador) con casos: crear comentario sin verificar
   ⇒ deny; 2 comentarios en <30 s ⇒ deny el 2º; reportar 2 veces el mismo ⇒ deny;
   reportar sin crear el doc de report en la transacción ⇒ deny; 3er reporte pone
   `oculto=true`; autor no puede reportarse; update de `texto` ⇒ deny; leer ocultos
   ⇒ deny. Si el emulador no está disponible en la máquina, dejar los tests escritos y
   documentar cómo correrlos (`firebase emulators:exec --only firestore "npm test"`).
   Estos archivos npm NO entran al build del sitio.
4. **Build:** `python -m sibylla.cli --html` → verificar `#social-conteos` en el HTML,
   orden aplicado (o editorial si Firestore no respondió) y `web/social.js` copiado.

## Paso 10 — Verificación manual E2E (con el sitio servido en local + Firestore real)

1. **Contadores:** en incógnito, los números (incluido 0) se ven al instante en todas
   las tarjetas (JSON horneado); con red lenta no hay huecos.
1b. **Función de conteos:** votar → en segundos `agregados/conteos` refleja el `{l,d,c}`
   correcto (verlo en la consola de Firestore); quitar el voto lo decrementa; ocultarse
   un comentario (3 reportes) decrementa `c`. Revisar los logs de la función: sin
   errores ni `failed-precondition`.
2. **Orden:** votar de forma que cambie el podio de una sección → recargar → cortina
   breve → la más votada primero, la más rechazada 6ª. Recarga <30 min (caché) → sin
   cortina ni saltos. Modo aleatorio → sin reorden. Selector de tarjetas en 3 → se ven
   las 3 mejores del nuevo orden.
3. **Comentarios:** cuenta Google (verificada) publica; cuenta email sin verificar ve
   el aviso y el botón de reenvío; tras verificar + recargar, publica; email sin nombre
   pasa por el flujo de apodo una sola vez. 2 comentarios seguidos en <30 s → toast de
   rate-limit. Borrar propio funciona. XSS: comentar
   `<img src=x onerror=alert(1)>` y `<script>alert(1)</script>` → se ven como texto
   literal, nada se ejecuta.
4. **Reportes:** con 3 cuentas distintas reportar el mismo comentario → al 3º
   desaparece para todos (recarga); 4ª cuenta ya no lo ve; el doc queda en consola con
   `oculto=true`. Reportar dos veces con la misma cuenta → no-op silencioso.
5. **Avatar:** con Google se ve la foto en el círculo dorado; con email, la inicial;
   popover con nombre+correo; alineado en 375/480/1280 px.
6. **Degradación:** bloquear `gstatic.com` → sitio idéntico al estático, sin cortina,
   sin errores.
7. **Producción (`https://sibylla.cl`):** login, voto, comentario, reporte y CSP sin
   violaciones en consola.

## Riesgos y salidas de escala

- **Costo de lecturas:** con el doc de conteos, un visitante frío cuesta **1 read** (+
  los docs de comentarios que abra). La cuota gratis de 50K reads/día alcanza para
  ~50K visitantes: el free tier deja de ser la restricción. El costo real ahora es
  **por escritura** (cada voto dispara la función: 1-2 reads + 1 write) — también
  despreciable a esta escala.
- **Blaze sin tope duro:** el riesgo económico existe (por eso las alertas de
  presupuesto del Paso 3 son obligatorias y App Check sube de valor). Si una alerta
  salta, el rollback barato es desactivar la función y volver a leer solo el JSON
  horneado en el build.
- **Contención del doc único `agregados/conteos`:** Firestore sostiene ~1 write/s por
  documento; ráfagas de votos simultáneos harían reintentar a la función. A la escala
  actual es teórico. Salida: partir en docs por sección o por tarjeta
  (`agregados/{cardId}`) — el cliente pasaría de 1 read a N, sigue siendo barato.
- **Deriva del contador:** los triggers son at-least-once; el diseño recalcula (no
  incrementa), así que cualquier valor raro se corrige con la siguiente interacción en
  esa tarjeta. Si algo se ve mal, re-correr `functions/seed.js`.
- **`getAfter`/transacciones en reglas** son la parte más frágil: no publicar la v2 sin
  pasar los tests del emulador. Si algo bloquea en producción, las reglas v1 de `votes`
  siguen siendo un rollback válido (los comentarios quedarían deshabilitados de facto:
  el cliente debe tolerar `permission-denied` sin romper).
- **Índices compuestos:** primera query/aggregation de comentarios fallará hasta crear
  el índice desde el enlace del error. El cliente ya trata ese fallo como "sin datos".
- **CSP vs. Google Auth:** el popup usa iframes/redirects que cambian con el tiempo;
  si rompe, retirar el header es un deploy de `static/.htaccess` (rápido) y se
  re-introduce afinado.
- **Brigading:** N cuentas verificadas pueden hundir una tarjeta al 6º puesto — pero
  nunca sacarla de portada (la selección es editorial) ni ocultar comentarios sin 3
  cuentas. App Check en monitor da visibilidad; el enforcement (Apéndice) sube el costo
  del ataque. Aceptado para el tamaño actual de la comunidad.
- **Conteo de comentarios y ocultamiento:** ocultar un comentario cambia el puntaje de
  la tarjeta en el próximo build/carga (cuenta solo visibles): comportamiento deseado.

## Apéndice — Enforcement de App Check (fase posterior, NO en esta)

Cuando App Check lleve ≥1-2 semanas en monitor con métricas limpias:
1. Crear un **service account** de solo lectura (`roles/datastore.viewer`) en el
   proyecto GCP `sibylla-a81d2`; guardar el JSON como secret de GitHub Actions
   (`FIREBASE_SA_KEY`) — jamás en el repo.
2. Migrar `social_sync.py` de REST anónimo a REST autenticado con token OAuth del SA
   (dep. `google-auth`), manteniendo el fallo aislado.
3. Activar enforcement de Firestore en la consola. Desde ahí, el REST anónimo (curl con
   la apiKey) queda bloqueado: solo la web con reCAPTCHA válido y el SA del build leen.
   La Cloud Function no se ve afectada (el Admin SDK no pasa por App Check).
