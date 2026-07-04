# PLAN-SOCIAL.md — Fase social 1: votos (like/dislike) + comentarios (teaser) + login Firebase

> Documento de implementación para agentes. Ejecutar los pasos en orden. El autor
> revisa la implementación al final. Comentarios y docs en **español** (convención del
> repo). **Nunca** subir `.env` ni imprimir claves; la `firebaseConfig` NO es secreto
> (ver más abajo) y sí va commiteada.

## Contexto

Sibylla es hoy un agregador de noticias 100 % estático (`web/index.html` generado por el
pipeline Python desde `sibylla/templates/index.html.j2`). Arranca la evolución "de
agregador a ágora" (Fase 1): en cada tarjeta, junto a los botones **Resumen / Original**
(que quedan a la izquierda), añadir a la derecha **3 botones compactos** — like, dislike y
comentarios — que invitan a crear cuenta / iniciar sesión al pulsarlos. Login con
**Firebase Auth** (Google + email/contraseña); votos persistidos en **Firestore**.

Principios (respetar): el núcleo estático se conserva; lo social se **hidrata por JS con
degradación elegante** (si Firebase no carga, el sitio queda idéntico a hoy). El `cardId`
de Firestore es `c.id` = `"n-" + sha256(dedup_key)[:12]` (`_card_id`, `sibylla/web.py`
L430-433), estable entre rebuilds. **La `firebaseConfig` NO es un secreto** (identifica el
proyecto; la seguridad vive en las reglas de Firestore + dominios autorizados): va
commiteada en la plantilla, a diferencia de las claves de `.env`.

### Decisiones ya tomadas (no re-litigar)
- **Alcance:** botones + login + **votos funcionales** en Firestore con contadores
  visibles. El botón de comentarios abre panel pero publicar queda "próximamente"
  (comentarios = Fase 2, exigen moderación/sanitización de UGC).
- **Reading gate desde ya:** like/dislike nacen atenuados y se habilitan cuando el usuario
  despliega el Resumen o hace clic en Original / título / imagen de la tarjeta. Es la
  decisión de producto central ("la lectura precede a la opinión"). El estado "leído" por
  tarjeta se guarda en `localStorage` (preferencia local, no estado compartido).
- **Login email:** email + contraseña (con reset). Google por `signInWithPopup`.
- **Locales solo-es:** la web se genera solo en español (`ALL_LANGS = ["es"]`) y
  Stellar-View traduce por LLM (`translate_cards`), no leyendo los `web.*` estáticos de
  `en/it/pt.json` (código muerto). Las claves nuevas van **solo a `es.json`** y se
  **recorta el test de paridad**. NO borrar archivos ni tocar el resolver (`resolve_lang`
  valida existencia y `test_i18n.py` depende de it/pt).
- **JS social en archivo externo:** `static/social.js` (módulo ES), no inline. Se despliega
  con el copiado de `static/`, cacheable, sin choques Jinja/JS, y es la "isla" que un día
  podría frameworkizarse (ver nota de arquitectura).

## Archivos afectados

- **`sibylla/templates/index.html.j2`** (fuente de verdad; `web/index.html` es salida):
  macro `tarjeta()` L50-66 (botones sociales), header `.barra .fila` L619-636 (chip de
  sesión), bloque `<style>` L111-613 (CSS social/modal), HTML del modal tras `#onboarding`
  (L808), un `<script type="application/json" id="social-i18n">` con textos ES +
  `firebaseConfig`, y `<script type="module" src="social.js?v={{ build_v }}">` tras el
  `</script>` existente (L1482). `build_v` (epoch del build) ya está en el contexto del
  template (L1100) y sirve de cache-buster.
- **`static/social.js`** (nuevo) — módulo ES con toda la lógica social. `_copy_static_assets`
  (`web.py` L1222-1237) lo copia a `web/social.js` sin tocar código.
- **`locales/es.json`** — solo aquí van las claves nuevas bajo `web.*`. `en/it/pt.json`
  no se tocan.
- **`tests/test_locales.py`** — recortar: la paridad estructural total (`web.*` completo)
  se exige solo en `es`; sacar it/pt/en del chequeo estructural. Los otros tests
  (existencia/JSON válido, `web.topics` y `web.months` en los 4) se conservan.
- **`firestore.rules`** (nuevo, raíz del repo) — fuente de verdad de las reglas.
- **`static/.htaccess`** — añadir `js|css` al `FilesMatch` de caché 7 días (ver §1c).
- No tocar `sibylla/web.py` (ya inyecta `c.id` y `build_v`), ni `i18n.py`, ni el resolver,
  ni `test_i18n.py`, ni el workflow.

## Decisiones técnicas

- **Contadores:** una `getAggregateFromServer` por tarjeta con
  `{ total: count(), suma: sum('value') }` sobre `where('card','==',id)` →
  `likes=(total+suma)/2`, `dislikes=(total-suma)/2`. Una query por tarjeta (verdad del
  suelo, votos = única fuente), no counter-doc con transacciones. Requiere índice compuesto
  `votes(card ASC, value ASC)`.
- **Coste / free tier (Spark, 50K reads/día):** contadores **lazy** con
  `IntersectionObserver` (solo tarjetas que entran al viewport) + cache en `sessionStorage`
  TTL ~30 min.
- **Modelo:** colección `votes`, doc id `` `${cardId}_${uid}` ``,
  `{card, uid, value: 1|-1, ts: serverTimestamp()}`. `setDoc` crea/cambia voto, `deleteDoc`
  lo quita (re-clic en el mismo = quitar). Un voto por usuario/tarjeta por construcción del id.
- **Voto optimista:** actualizar UI/contador al instante, revertir si la escritura falla;
  deshabilitar el par like/dislike mientras hay un write en vuelo (anti doble-clic).
- **Degradación:** botones sociales y chip de sesión se renderizan ocultos (`display:none`);
  tras init Firebase OK, el módulo añade `body.social-on` que los revela. Import dinámico
  del SDK en `try/catch`: si gstatic falla o el navegador no soporta módulos ES, el body
  nunca recibe la clase y el sitio queda como hoy.
- **Ubicación del JS y cache-busting:** `static/social.js` (JS plano, sin Jinja),
  referenciado con `<script type="module" src="social.js?v={{ build_v }}">`. El `?v=`
  per-deploy protege contra la caché (heurística hoy; explícita de 7 días tras §1c) y
  contra cualquier CDN por delante. Los datos que dependen del build (textos ES de i18n +
  `firebaseConfig`) NO se hornean en el .js: viajan en un
  `<script type="application/json" id="social-i18n">` que la plantilla rellena y el módulo
  lee al arrancar. Así `social.js` queda libre de Jinja y cacheable.
- **Nota de arquitectura (no acción):** no se adopta framework ahora. `social.js` se
  estructura como **módulo de hidratación acotado** (monta sobre `.soc-grupo`, `#auth` y
  `#sesion`), para que la Fase 2 (comentarios con hilos, realtime `onSnapshot`) pueda
  reescribir SOLO ese módulo con arquitectura de islas (Alpine / Preact+htm / Lit por CDN,
  o Astro con build step) sin tocar el generador Python ni el shell estático.

## Pasos de implementación

### 1. Claves de locales (solo `es.json`, sección `web`)
Añadir bajo `web`:
`social_like`, `social_dislike`, `social_comments`, `social_gate_hint`,
`social_comments_soon`, `social_vote_error`, `auth_title`, `auth_sub_vote`,
`auth_sub_comment`, `auth_google`, `auth_or`, `auth_email`, `auth_password`, `auth_signin`,
`auth_signup`, `auth_to_signup`, `auth_to_signin`, `auth_forgot`, `auth_reset_sent`,
`auth_close`, `auth_enter`, `auth_logout`, `auth_err_invalid_email`, `auth_err_wrong`,
`auth_err_email_in_use`, `auth_err_weak_password`, `auth_err_popup`, `auth_err_network`,
`auth_err_too_many`, `auth_err_generic`.

Valores ES sugeridos: like «Me gusta», dislike «No me gusta», comments «Comentarios»,
gate_hint «Lee la noticia para votar», comments_soon «Los comentarios llegan pronto.
Sibylla está preparando el ágora.», vote_error «No se pudo registrar tu voto. Intenta de
nuevo.», auth_title «Únete a la conversación», sub_vote «Inicia sesión para votar.»,
sub_comment «Inicia sesión para comentar.», google «Continuar con Google», or «o con tu
correo», email «Correo», password «Contraseña», signin «Entrar», signup «Crear cuenta»,
to_signup «¿No tienes cuenta? Regístrate», to_signin «¿Ya tienes cuenta? Entra», forgot
«Olvidé mi contraseña», reset_sent «Te enviamos un enlace para restablecer tu contraseña.»,
close «Cerrar», enter «Entrar», logout «Cerrar sesión»; errores: invalid_email «El correo
no es válido.», wrong «Correo o contraseña incorrectos.», email_in_use «Ese correo ya tiene
cuenta. Prueba a entrar.», weak_password «La contraseña debe tener al menos 6 caracteres.»,
popup «El navegador bloqueó la ventana de Google. Permite pop-ups e intenta de nuevo.»,
network «Sin conexión con el oráculo. Revisa tu red.», too_many «Demasiados intentos.
Espera un momento.», generic «Algo falló. Intenta de nuevo.».
Solo en `es.json`; `en/it/pt.json` no se tocan.

### 1b. Recortar `tests/test_locales.py`
La web solo renderiza `es` y Stellar traduce por LLM, así que exigir `web.*` completo en
en/it/pt protege archivos muertos. Cambios:
- Solo `test_locales_paridad_estructural_total` (el recursivo) necesita recorte: ya no
  exigir que en/it/pt tengan toda la estructura de es (documentar el porqué en el
  docstring: web solo-es + Stellar por LLM).
- `test_locales_mismas_top_level_keys` NO se toca: compara solo claves raíz (`web`, `cli`,
  ...) y añadir claves dentro de `web` no lo afecta.
- Conservar intactos: `test_locales_existen_y_son_json`, `test_web_topics_mismas_claves`,
  `test_web_months_12_entradas`.
- No tocar `test_i18n.py` ni `i18n.py` (el resolver sigue soportando en/it/pt).

### 1c. Caché de `.js` en `static/.htaccess`
Hoy el `FilesMatch` de 7 días solo cubre `png|ico|jpg|jpeg|svg|webmanifest`; `.js` cae a la
caché heurística del navegador. Añadir `js` y `css` a esa regla (línea 19 de
`static/.htaccess`), actualizando el comentario en español: los assets versionados por
query (`social.js?v=<build_v>`) pueden cachearse fuerte porque el `?v=` per-deploy invalida.
El `?v={{ build_v }}` sigue siendo obligatorio.

### 2. Macro `tarjeta()` — grupo social en `.carta-acciones` (L60-63)
Tras el botón Original, añadir `<span class="soc-grupo" data-card="{{ c.id }}">` con 3
`<button class="sec-btn soc-btn ...">`:
- like: `data-vote="1"`, `aria-pressed="false"`, `aria-label="{{ t.social_like }}"`,
  `title="{{ t.social_gate_hint }}"` + `<span class="soc-num" data-num="like"></span>`.
- dislike: `data-vote="-1"`, análogo, `data-num="dislike"`.
- comentarios: `class="sec-btn soc-btn soc-com"`, `aria-expanded="false"`,
  `aria-label="{{ t.social_comments }}"`.
Iconos SVG inline 16×16 (`fill:none; stroke:currentColor; stroke-width:1.7`, heredando
`.sec-btn svg`, L240-241): pulgar arriba / pulgar abajo / globo de diálogo. Usar la skill
**frontend-design** para afinar los glifos y que encajen con la estética grecorromana/sci-fi.

### 3. CSS (bloque `/* ---- Social ---- */` tras `.resumen-panel[hidden]`, L403)
- `.soc-grupo{ display:none; margin-left:auto; align-items:center; gap:10px; }` +
  `body.social-on .soc-grupo{ display:inline-flex; }` (empuja el grupo a la derecha;
  Resumen/Original quedan a la izquierda).
- `.soc-item{ display:inline-flex; align-items:center; gap:4px; }`
  `.soc-num{ min-width:1.1em; font-size:.72rem; color:var(--tenue); font-variant-numeric:tabular-nums; }`
- `.soc-btn` extiende `.sec-btn` (círculo 28px). Gate `.soc-btn.is-locked{ opacity:.22; box-shadow:none; }`
  + neutralizar su hover (clicable solo para mostrar el tooltip).
- Voto activo (mismo lenguaje que `.carta.resumen-open .btn-resumen`):
  `.soc-like[aria-pressed="true"]{ color:#1a1305; background:linear-gradient(180deg,#F2DD93,#D9B85F); border-color:#F2DD93; }`
  `.soc-dislike[aria-pressed="true"]{ color:#2a120b; background:linear-gradient(180deg,#E7A38F,#C97B62); border-color:#E7A38F; }`
- Comentarios = futuro → hover cian: `.soc-com:hover{ color:var(--cian-claro); border-color:rgba(94,230,224,.6); background:rgba(94,230,224,.10); }`
- `.comentarios-panel` reutiliza props de `.resumen-panel` con tinte cian.
- Media 480px (L575-612): `.soc-grupo{ gap:6px }`. `.carta-acciones` ya tiene `flex-wrap:wrap`,
  así que envuelve sin romperse.

### 4. Header — indicador de sesión (`.barra .fila`, L619-636)
`<span class="sesion" id="sesion">` (oculto salvo `body.social-on`) **antes** del
`input#menu-toggle` (fuera de `nav.menu`, visible en móvil sin abrir la hamburguesa):
enlace **Entrar** (`#sesion-entrar`), chip circular con la inicial del usuario
(`#sesion-chip`, estilo `.sec-btn`, Cinzel dorado, `hidden` por defecto) y popover
(`#sesion-menu`, `hidden`) con correo + botón **Cerrar sesión** (`#sesion-salir`).
CSS: `.sesion{ display:none } body.social-on .sesion{ display:inline-flex }`.

### 5. Modal de auth (HTML tras `#onboarding`, L808)
`<div id="auth" class="onb onb--auth" role="dialog" aria-modal="true" aria-labelledby="auth-titulo" hidden>`
reutilizando `.onb`/`.onb-panel` (patrón onboarding) con panel más estrecho (max-width ~440px):
- `#auth-titulo` (`{{ t.auth_title }}`), subtítulo `#auth-sub` con
  `data-vote="{{ t.auth_sub_vote }}"` y `data-comment="{{ t.auth_sub_comment }}"`.
- Botón `#auth-google` (Google, con logo G monocromo stroke currentColor).
- Separador `.auth-sep` con `{{ t.auth_or }}`.
- `<form id="auth-form" novalidate>`: `#auth-email` (type=email, autocomplete=email,
  required), `#auth-pass` (type=password, autocomplete=current-password, required minlength=6),
  `<p id="auth-msg" role="alert" hidden>`, submit `#auth-submit`.
- `.auth-links`: `#auth-alternar` (registro/entrar) y `#auth-olvide` (olvido).
- `#auth-cerrar`.
Nueva regla `body.auth-abierto{ overflow:hidden }` (NO reutilizar `onb-abierto`). Estilos de
inputs/separador/links coherentes con la paleta (fondos `rgba(217,184,95,.05)`, focus dorado,
error `#E7A38F`, éxito `--cian-claro`).

### 6. Módulo JS — `static/social.js` (nuevo) + wiring en la plantilla
En `index.html.j2`, tras el `</script>` ES5 existente (L1482), añadir SOLO:
- `<script type="application/json" id="social-i18n">` con `{{ ... | tojson }}`: los textos
  ES (`social_*`, `auth_*`) y el `firebaseConfig` (público por diseño — comentario en la
  plantilla dejándolo explícito, va commiteado a diferencia de `.env`).
- `<script type="module" src="social.js?v={{ build_v }}"></script>` (NO tocar el ES5).

`static/social.js` = IIFE async (ES2017+, filtrado por `type="module"`):
0. **Carga defensiva:** `import()` dinámico de firebase-app/auth/firestore desde
   `gstatic.com` (SDK modular v10.x ≥10.5 por `sum()`) en `try/catch`; si falla, `return`.
1. **Init:** leer `#social-i18n` (config + textos); init app/auth/db; si algo lanza, `return`
   silencioso; en éxito `body.classList.add('social-on')`.
2. **Reading gate:** `localStorage 'sibylla_leidas'` (`{cardId:1}`, tope FIFO ~500). Listener
   delegado que marca leída al pulsar `.btn-resumen`, `.btn-original`, título o imagen de la
   `.carta`, y desbloquea su `.soc-grupo` (quita `.is-locked`, cambia el title).
3. **Estado:** usuario actual; `miVoto` Map `cardId→1|-1` (query `where('uid','==',uid)` al
   loguear, cacheada por sesión); `conteos` Map.
4. **Contadores lazy:** `IntersectionObserver` (rootMargin 300px) → cache sessionStorage
   TTL 30 min → miss dispara la aggregation → pinta `.soc-num`. Error de índice/offline:
   números vacíos + `console.warn` una vez, sin romper.
5. **Votar:** click delegado: `is-locked` → no-op; sin usuario → `abrirAuth('vote')`; con
   usuario → optimista + `setDoc`/`deleteDoc`, revert + `social_vote_error` si falla.
6. **Comentarios (teaser):** sin usuario → `abrirAuth('comment')`; con usuario → toggle de
   `div.comentarios-panel` on-demand con `social_comments_soon`.
7. **Modal auth:** `abrirAuth(motivo)` fija subtítulo (data-vote/data-comment), muestra
   `#auth`, focus + trampa de Tab + Escape (patrón onboarding L1406-1416). Google:
   `signInWithPopup` **directo en el handler** (gesto de usuario, evita bloqueo de pop-ups);
   ignora `popup-closed-by-user`. Form modo entrar/registrar (toggle cambia textos y
   autocomplete); `sendPasswordResetEmail` para olvido; `mapearError(code)` → claves
   `auth_err_*` (invalid-email, invalid-credential/wrong-password/user-not-found→wrong,
   email-already-in-use, weak-password, popup-blocked, network-request-failed,
   too-many-requests, default→generic).
8. **Sesión:** `onAuthStateChanged` pinta chip/Entrar, cierra el modal si estaba abierto,
   carga `miVoto` y pinta `aria-pressed`; `#sesion-salir` → `signOut`.

### 7. `firestore.rules` (nuevo, raíz)
```
rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    // Votos: doc id = <cardId>_<uid>. Un voto por usuario y tarjeta por construcción.
    // read público: lo requieren las aggregations de contadores y la carga de los votos
    // propios (uid seudónimo, sin datos personales).
    match /votes/{voteId} {
      allow read: if true;
      allow create: if request.auth != null
        && request.resource.data.keys().hasOnly(['card', 'uid', 'value', 'ts'])
        && request.resource.data.uid == request.auth.uid
        && voteId == request.resource.data.card + '_' + request.auth.uid
        && request.resource.data.card is string
        && request.resource.data.card.size() >= 3
        && request.resource.data.card.size() <= 64
        && request.resource.data.value in [1, -1]
        && request.resource.data.ts == request.time;
        // DECISIÓN Fase 1: verificación de correo NO obligatoria (comunidad inicial
        // pequeña; Google llega verificado; exigirla al flujo email añade fricción).
        // Para endurecer ante spam: descomentar la línea siguiente Y además
        // implementar sendEmailVerification tras el registro + UX "revisa tu correo".
        // && request.auth.token.email_verified == true
      allow update: if request.auth != null
        && resource.data.uid == request.auth.uid
        && request.resource.data.keys().hasOnly(['card', 'uid', 'value', 'ts'])
        && request.resource.data.card == resource.data.card
        && request.resource.data.uid == resource.data.uid
        && request.resource.data.value in [1, -1]
        && request.resource.data.ts == request.time;
      allow delete: if request.auth != null
        && resource.data.uid == request.auth.uid;
    }
    match /{document=**} { allow read, write: if false; }
  }
}
```

### 8. Consola Firebase — estado y pendientes
Proyecto **ya creado**: `sibylla` (ID `sibylla-a81d2`), plan Spark. **Google** y
**Correo/contraseña** ya habilitados (verificado). Falta:
1. Authentication → Settings → **Authorized domains**: verificar `localhost` y añadir
   `sibylla.cl` y `www.sibylla.cl` (y `127.0.0.1` si se prueba con esa IP).
2. **Firestore Database** → crear en modo **producción**, ubicación `southamerica-west1`
   (Santiago; permanente).
3. Firestore → **Reglas**: pegar `firestore.rules` del repo → Publicar.
4. Firestore → **Índices**: NO crear nada a mano de antemano. Verificación empírica: si la
   1ª aggregation real falla con `failed-precondition`, el SDK imprime en la consola del
   navegador un enlace que crea el índice compuesto `votes(card ASC, value ASC)` con un
   clic. (Es probable que haga falta: `sum()` opera sobre index entries y el campo agregado
   debe estar en el índice del query; `count()` solo no lo requeriría.)
5. **Configuración del proyecto → Tus apps → web `</>`** → registrar `sibylla-web` (sin
   Hosting) → copiar el objeto `firebaseConfig` → pegarlo en el `#social-i18n` de la
   plantilla. (Pendiente entregar el objeto; es público, no secreto.)
6. *(Opcional futuro)* App Check con reCAPTCHA v3 en modo monitor primero.

## Verificación

1. `pytest tests/test_locales.py tests/test_i18n.py` en verde (paridad recortada a es;
   resolver intacto), luego la suite completa.
2. Build: `python -m sibylla.cli --html` → confirmar que `web/social.js` se copió y que el
   HTML lo referencia con `?v=<build_v>` y trae el `#social-i18n`. Servir
   `python -m http.server 8000 --directory web` y abrir `http://localhost:8000` (nunca
   `file://`: Auth exige http/https).
3. **Degradación** (bloqueando `gstatic.com` en DevTools o sin `firebaseConfig`): página
   idéntica a hoy, sin botones sociales, sin "Entrar", sin errores rompedores; el acordeón
   Resumen y el JS ES5 siguen funcionando.
4. **Gate:** con sesión, like/dislike atenuados con tooltip; desplegar Resumen o clicar
   Original/título/imagen los habilita; recargar y verificar persistencia (`sibylla_leidas`).
5. **Flujo completo:** registro email (+ error si contraseña <6), login Google por popup,
   olvido de contraseña (llega correo), like → doc `votes/n-xxxx_uid` con value 1, re-clic
   lo borra, cambio a dislike lo actualiza; contadores visibles en incógnito; logout limpia
   `aria-pressed`.
6. **Contadores:** 1ª carga dispara `:runAggregationQuery` solo para tarjetas visibles;
   recarga <30 min sirve de sessionStorage (sin requests).
7. **Responsive** 480/375px: la fila envuelve, chip de sesión visible, modal usable.
8. **Deploy:** en `https://sibylla.cl` probar el popup de Google (dominio autorizado).

## Riesgos

- **Safari/ITP:** popup (no redirect); lanzarlo síncrono en el handler (sin `await` previo)
  o Safari lo bloquea; ignorar `popup-closed-by-user`.
- **Free tier (50K reads/día):** las aggregations se facturan a **1 read por cada lote de
  hasta 1.000 index entries** que casan la query (mínimo 1) — es decir, 1 read por tarjeta
  visible/visitante frío mientras cada tarjeta tenga <1.000 votos; NO 1 read por voto.
  Contenido por TTL + IntersectionObserver. Salidas de escala si el tráfico crece:
  (a) JSON de contadores pre-agregado en el build (el cron corre 2×/día; staleness ≤12 h
  como piso + aggregation en vivo encima), o (b) migrar a Blaze + Cloud Function
  `onWrite(votes)` que mantenga `cards/{id}.{likes,dislikes}`. OJO: Cloud Functions
  **requiere plan Blaze** (las 2M invocaciones/mes gratis son dentro de Blaze, no de
  Spark) — no asumir Functions mientras el proyecto siga en Spark.
- **Privacidad:** `read: if true` en `votes` permite listar TODOS los votos de todos los
  uids con un simple `collection('votes').get()` (uids seudónimos, sin datos personales).
  Inevitable sin backend intermedio en Spark; aceptable en Fase 1, documentado en las
  reglas. Evolución futura (Fase 2, requeriría Blaze): gateway por Cloud Function que
  exponga solo agregados. Mitigación de bots autenticados votando en masa: App Check
  (§8.6, opcional) y/o el flag `email_verified` de las reglas.
- **Índice compuesto ausente:** la 1ª aggregation falla con `failed-precondition`; el módulo
  lo trata como "sin contadores" sin romper.
- **CSP futura:** hoy no hay; anotar que deberá permitir `www.gstatic.com`, `*.googleapis.com`
  (identitytoolkit/securetoken/firestore) y `apis.google.com`.
- **No romper el ES5:** cero cambios en el script existente; `social.js` usa ES2017+ (lo
  filtra `type="module"`). Prefijos `soc-`/`auth-`/`sesion-` verificados inexistentes.
- **Cache de `social.js`:** el `?v={{ build_v }}` es imprescindible — con la regla de §1c
  el `.htaccess` servirá el JS cacheado hasta 7 días tras un deploy si falta el query.
  Verificar que el `?v=` aparece en el HTML generado.
- **Votos huérfanos:** tarjetas que salen de portada dejan votos en Firestore (derivados de
  `dedup_key`, sobreviven a rebuilds); inofensivo, limpieza opcional futura.
