# PLAN-SOCIAL3.md — Fase social 3: hilos de respuestas + likes/dislikes en comentarios

> Documento de implementación para agentes. Leer **AGENTS.md**, **PLAN-SOCIAL.md** y
> **PLAN-SOCIAL2.md** antes de empezar: este plan asume la Fase 2 ya en producción
> (reglas v2 publicadas, Cloud Function de conteos desplegada, índices `comments` y
> `votes(card,value)` creados). Cada paso deja el repo en verde (tests + build) antes
> del siguiente. Comentarios y docs en **español**. **Nunca** subir `.env` ni imprimir
> claves (la `firebaseConfig` del template NO es secreto). Los commits llevan
> `Co-Authored-By` del modelo que realmente opera (ver AGENTS.md).

## Contexto y estado actual

La Fase 2 dejó: comentarios planos por tarjeta (más reciente primero, 20/página,
máx. 500 chars, texto plano con `textContent`), reporte con auto-ocultado a los 3
reportes (100 % reglas), borrado del comentario propio (`deleteDoc`), rate-limit por
`users/{uid}` (30 s), conteos pre-agregados en `agregados/conteos` mantenidos por la
Cloud Function (recalcular-en-vez-de-incrementar, idempotente) y orden social de
tarjetas `(l−d)+2c`. App Check en modo monitor; proyecto `sibylla-a81d2` en Blaze con
alertas de presupuesto $1/$5.

**Qué añade la Fase 3:**
1. **Hilos de 1 nivel:** responder a un comentario raíz crea una respuesta dentro de
   su rama (estilo YouTube). No hay respuestas de respuestas: el botón «Responder» de
   una respuesta apunta a la misma rama (misma raíz).
2. **Likes/dislikes en comentarios:** un voto por usuario por comentario, con
   contadores visibles. Los votos NO reordenan los comentarios (siguen cronológicos).
3. **Borrado con placeholder:** borrar un comentario raíz que ya tiene respuestas lo
   convierte en «Comentario eliminado» (sin autor ni texto); las respuestas de otros
   sobreviven. Sin respuestas, el borrado sigue siendo real.

## Decisiones del autor (2026-07-08, no re-litigar)

- **Profundidad:** 1 nivel. Raíces y respuestas; jamás anidamiento más profundo.
- **Orden:** cronológico. Raíces más recientes primero (como hoy); respuestas más
  antiguas primero dentro de su rama. Los votos de comentarios se muestran pero no
  ordenan (los conteos quedan denormalizados: pasar a «mejores primero» después es
  barato, fuera de alcance ahora).
- **Borrado de raíz con respuestas:** placeholder «Comentario eliminado» (se vacían
  `texto` y `autor` — cumple el derecho de eliminación de datos personales sin
  destruir contenido ajeno). Las respuestas, al no tener hijas, se borran de verdad.
- **Puntaje de tarjeta:** sin cambios. Las respuestas visibles cuentan en `c` igual
  que las raíces (la función ya cuenta todos los comentarios visibles de la tarjeta);
  los votos de comentarios NO tocan el puntaje ni el orden social de tarjetas.

## Decisiones técnicas de este plan

- **Votar comentarios NO exige correo verificado** (paridad con los votos de tarjeta,
  decisión de Fase 1 que no se re-litiga). Sí exige sesión. Comentar/responder sigue
  exigiendo correo verificado.
- **Un voto por usuario por comentario** por construcción del doc id
  (`<commentId>_<uid>`), mismo patrón que `votes`.
- **Conteos por comentario denormalizados en el propio doc** (`l`, `d`, `respuestas`),
  mantenidos por la Cloud Function con Admin SDK (salta las reglas; ningún cliente
  puede escribirlos). Así la lista de comentarios trae sus votos sin reads extra.
- **Rate-limit de respuestas:** el mismo gate de 30 s de `lastCommentAt` (una
  respuesta ES un comentario). Los votos de comentarios no llevan rate-limit (como
  los de tarjeta); App Check monitor + alertas de presupuesto cubren el abuso.
- **Reportes sobre respuestas:** mecanismo intacto (el update de reporte no mira
  `parent`). Una respuesta auto-ocultada descuenta `respuestas` de su raíz (función).
- **Sin cambios en Python** (`web.py`, `social_sync.py`, fórmula, template de orden):
  la semántica de `agregados/conteos` no cambia. Tampoco cambian CSP ni App Check.

## Modelo de datos (contrato — TODOS los carriles programan contra esto)

```
comments/{autoId}:
  card: string            (igual que hoy)
  uid, autor, texto, ts, reportes, oculto   (igual que hoy)
  parent: string | null   (NUEVO, obligatorio en creación: null = raíz,
                           commentId de una raíz = respuesta)
  eliminado?: bool        (NUEVO, solo vía update de auto-borrado del dueño)
  l?, d?: int             (NUEVO, solo los escribe la Cloud Function)
  respuestas?: int        (NUEVO, solo en raíces, solo la Cloud Function:
                           respuestas visibles y no eliminadas)

commentVotes/{commentId}_{uid}:               (colección NUEVA)
  comment: string         (id del comentario)
  card: string            (card real del comentario; las reglas lo verifican
                           con get() — habilita la query «mis votos por tarjeta»)
  uid: string
  value: 1 | -1
  ts: serverTimestamp
```

Queries del cliente (definen los índices):
- Raíces: `comments` where `card == X && oculto == false && parent == null`
  orderBy `ts desc`, `limit 20` + `startAfter` → índice
  `comments(card ASC, oculto ASC, parent ASC, ts DESC)`.
- Respuestas (al expandir una rama): where `parent == Y && oculto == false`
  orderBy `ts asc`, `limit 20` + `startAfter` → índice
  `comments(parent ASC, oculto ASC, ts ASC)`.
- Mis votos de comentarios (al abrir un panel): `commentVotes` where
  `uid == me && card == X` → índice `commentVotes(uid ASC, card ASC)` (o index
  merging; crear solo si falla).

**Migración obligatoria:** los comentarios existentes NO tienen `parent`; la query de
raíces con `parent == null` no los devolvería. El backfill del Paso 4c les pone
`parent: null` antes de desplegar el cliente nuevo.

## Archivos afectados

- `firestore.rules` — v3: `parent` en create, update de auto-borrado, `commentVotes`.
- `tools/rules-tests/test-rules.js` — casos nuevos + actualizar helpers (todo
  comentario nuevo lleva `parent`).
- `functions/index.js` — trigger `commentVotes`, denormalización `l/d/respuestas`,
  guardia anti-bucle, limpieza al borrar raíz.
- `functions/seed.js` — contar solo comentarios no eliminados.
- `functions/backfill-hilos.js` (**nuevo**) — migración one-shot.
- `locales/es.json` — claves nuevas (solo `es`, como siempre).
- `sibylla/templates/index.html.j2` — claves en `#social-i18n` (~L1739-1747) + CSS de
  hilos/votos en el bloque de comentarios (~L433-463).
- `static/social.js` — hilos, votos de comentarios, borrado con placeholder.
- `tests/` — sin cambios de Python previstos; la suite debe seguir verde.
- **No tocar:** `sibylla/web.py`, `sibylla/social_sync.py`, el `<script>` ES5 del
  template, workflows, `.htaccess`.

---

## Orquestación con agentes

El contrato de datos de arriba está cerrado: los carriles A, B y C no comparten
archivos y pueden correr **en paralelo**. Cada agente recibe: «Lee AGENTS.md y
PLAN-SOCIAL3.md; ejecuta el Carril <X> (Pasos <n>). Deja el repo en verde.»

| Carril | Pasos | Archivos | Depende de |
|---|---|---|---|
| **A — Reglas** | 2, 3 | `firestore.rules`, `tools/rules-tests/` | — |
| **B — Función** | 4 | `functions/` | — |
| **C — Cliente** | 1, 5, 6 | `locales/es.json`, template, `static/social.js` | — |
| **D — Diseño** | 7 | template (CSS), `static/social.js` (solo clases/DOM) | C |
| **Autor (manual)** | 8 | consola Firebase, deploys | A, B |
| **E — Verificación** | 9, 10 | tests + E2E | todos |

- El Carril D **debe invocar la skill `frontend-design`** antes de tocar el CSS
  (misma práctica que el Paso 7 de Fase 2) y verificar con screenshots.
- Si un carril descubre que necesita cambiar el contrato, se detiene y lo reporta:
  el contrato solo lo cambia el autor.
- Integración: mergear A y B antes que C solo si hay conflicto (no debería); el
  Carril E corre en el repo ya integrado.

---

## Paso 1 — Claves de locales (`locales/es.json`, sección `web`)

Añadir (valores ES sugeridos; ajustar tono sibilino si se quiere):
- `social_reply` «Responder», `social_reply_placeholder` «Escribe tu respuesta…»,
  `social_reply_send` «Responder»
- `social_replies_show` «Ver respuestas ({n})», `social_replies_hide` «Ocultar
  respuestas»
- `social_comment_deleted` «Comentario eliminado»
- `social_comment_like_aria` «Me gusta este comentario ({n})»,
  `social_comment_dislike_aria` «No me gusta este comentario ({n})»
- `social_comment_vote_error` «No se pudo registrar tu voto. Intenta de nuevo.»

`en/it/pt.json` NO se tocan. Exponer todas las claves nuevas en el JSON
`#social-i18n` del template (~L1739, seguir el patrón existente).

## Paso 2 — `firestore.rules` v3

Cambios sobre la v2 actual (los bloques `votes`, `reports`, `users` y `agregados`
quedan **idénticos**):

**2a. `comments` create — hilos.** Añadir `'parent'` al `hasOnly` de keys y exigir:

```
&& 'parent' in request.resource.data
&& (request.resource.data.parent == null
    || (request.resource.data.parent is string
        // La raíz existe, es de la misma tarjeta, es raíz (1 nivel máx.),
        // está visible y no es un placeholder eliminado.
        && exists(/databases/$(database)/documents/comments/$(request.resource.data.parent))
        && get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data.card
           == request.resource.data.card
        && (!('parent' in get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data)
            || get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data.parent == null)
        && get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data.oculto == false
        && (!('eliminado' in get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data)
            || get(/databases/$(database)/documents/comments/$(request.resource.data.parent)).data.eliminado == false)))
```

Presupuesto de accesos a documentos: `get`/`exists` del padre cuentan 1 (mismo doc)
+ `get`/`getAfter` de `users` = ≤4 de 10. OK. El resto del create no cambia
(verificado, rate-limit 30 s, `autor == token.name`, etc. — las respuestas heredan
todo eso).

**2b. `comments` update — reporte O auto-borrado.** El update actual (reporte) se
convierte en una de dos ramas unidas por `||`. Rama nueva (auto-borrado a
placeholder, solo el dueño):

```
|| (request.auth != null
    && request.auth.uid == resource.data.uid
    && request.resource.data.diff(resource.data).affectedKeys().hasOnly(['eliminado', 'texto', 'autor'])
    && request.resource.data.eliminado == true
    && request.resource.data.texto == ''
    && request.resource.data.autor == '')
```

**2c. `comments` delete — solo sin respuestas.** El borrado real queda restringido a
comentarios sin respuestas visibles (el campo lo mantiene la función; si no existe,
vale 0):

```
allow delete: if request.auth != null && resource.data.uid == request.auth.uid
  && (!('respuestas' in resource.data) || resource.data.respuestas == 0);
```

(Las respuestas nunca tienen `respuestas` > 0, así que siempre se borran de verdad.
Hay una carrera teórica — respuesta creada antes de que la función actualice el
contador — aceptada: la limpieza del Paso 4b la cubre.)

**2d. `commentVotes` — colección nueva.** Espejo de `votes` (sin verificación de
correo) + integridad contra el comentario real:

```
match /commentVotes/{voteId} {
  allow read: if true;
  allow create: if request.auth != null
    && request.resource.data.keys().hasOnly(['comment', 'card', 'uid', 'value', 'ts'])
    && request.resource.data.uid == request.auth.uid
    && voteId == request.resource.data.comment + '_' + request.auth.uid
    && request.resource.data.value in [1, -1]
    && request.resource.data.ts == request.time
    && exists(/databases/$(database)/documents/comments/$(request.resource.data.comment))
    && get(/databases/$(database)/documents/comments/$(request.resource.data.comment)).data.card
       == request.resource.data.card;
  allow update: if request.auth != null
    && resource.data.uid == request.auth.uid
    && request.resource.data.keys().hasOnly(['comment', 'card', 'uid', 'value', 'ts'])
    && request.resource.data.comment == resource.data.comment
    && request.resource.data.card == resource.data.card
    && request.resource.data.uid == resource.data.uid
    && request.resource.data.value in [1, -1]
    && request.resource.data.ts == request.time;
  allow delete: if request.auth != null && resource.data.uid == request.auth.uid;
}
```

Nota: los campos `l`, `d`, `respuestas` de `comments` NO aparecen en ningún `hasOnly`
de create/update de cliente — solo la función (Admin SDK) los escribe, y el update de
reporte usa `diff().affectedKeys()`, así que no choca con docs que ya los tengan.

## Paso 3 — Tests de reglas (`tools/rules-tests/test-rules.js`)

Actualizar helpers: `seedComment`/`createComment` añaden `parent: null` (y variantes
para respuestas). Casos nuevos mínimos:
1. Crear comentario **sin** el campo `parent` ⇒ deny (el contrato lo exige).
2. Responder a una raíz visible ⇒ allow; a una respuesta (parent de parent) ⇒ deny;
   a un padre inexistente ⇒ deny; con `card` distinta a la del padre ⇒ deny; a un
   padre oculto o eliminado ⇒ deny.
3. Auto-borrado: el dueño convierte a placeholder (`eliminado/texto/autor` exactos)
   ⇒ allow; otro usuario ⇒ deny; el dueño intentando dejar `texto` no vacío o tocar
   otras claves ⇒ deny.
4. Delete real: dueño con `respuestas: 0` o sin campo ⇒ allow; con `respuestas: 2`
   ⇒ deny.
5. `commentVotes`: crear con id `<comment>_<uid>` propio ⇒ allow (usuario NO
   verificado también ⇒ allow — paridad con `votes`); id ajeno ⇒ deny; `value: 2`
   ⇒ deny; comentario inexistente ⇒ deny; `card` que no coincide con la del
   comentario ⇒ deny; cambiar `value` (update) ⇒ allow; borrar el propio ⇒ allow.
6. El flujo de reporte de Fase 2 sigue pasando tal cual (regresión).

Correr con `firebase emulators:exec --only firestore "npm test"` (o dejar
documentado el comando si el emulador no está disponible en la máquina del agente).

## Paso 4 — Cloud Function y migración (`functions/`)

**4a. Trigger nuevo `onCommentVoteWritten('commentVotes/{voteId}')`.** Extrae
`comment` del doc (after o before), recalcula con aggregation
(`count` + `sum('value')` sobre `commentVotes` where `comment == id`, misma
aritmética `l/d` que `countVotes`) y escribe `{l, d}` en el doc del comentario
(`update`; si el comentario ya no existe, no-op silencioso). Idempotente, como todo
lo demás.

**4b. Extender `onCommentWritten`.** Con **guardia anti-bucle**: si el update solo
cambió claves denormalizadas (`l`, `d`, `respuestas`) — comparar before/after —
retornar temprano sin recalcular nada (las escrituras de la propia función no deben
re-disparar trabajo). Después de la guardia:
1. `recomputeCard(card)` como hoy, pero `countComments` pasa a excluir placeholders:
   contar `oculto == false` y restar los que tengan `eliminado == true` (segunda
   aggregation `where('oculto','==',false).where('eliminado','==',true)`; si
   Firestore pide índice, el enlace sale en los logs).
2. Si el doc escrito es una **respuesta** (`parent` string): recalcular
   `respuestas` de la raíz = count de `comments` where
   `parent == raíz && oculto == false` y escribirlo en la raíz (si la raíz ya no
   existe, no-op).
3. Si se **borró una raíz**: limpieza con Admin SDK — borrar sus `commentVotes`
   (where `comment == id`) y cualquier respuesta huérfana (where `parent == id`;
   normalmente ninguna: las reglas bloquean borrar con respuestas visibles). Si se
   **borró una respuesta**, borrar sus `commentVotes` y recalcular `respuestas` de
   la raíz.
4. Opcional (pulido): si tras borrar una respuesta la raíz es un placeholder
   (`eliminado == true`) con `respuestas == 0`, borrar el placeholder.

**4c. `functions/backfill-hilos.js` (nuevo, one-shot).** Con Application Default
Credentials (patrón de `seed.js`): a todo doc de `comments` sin campo `parent` le
escribe `parent: null`; inicializa `respuestas: 0` en raíces y `l: 0, d: 0` donde
falten (opcional: el cliente ya trata ausente como 0). Documentar en
`functions/README.md`: correr **una vez**, después de desplegar la función y ANTES
de publicar el cliente nuevo.

**4d. `seed.js`.** Alinear `countComments` con la nueva exclusión de `eliminado`
(mismo cálculo que 4b.1).

Validar en el emulador (`firebase emulators:start --only functions,firestore`):
votar un comentario actualiza su `l/d`; crear/borrar/ocultar una respuesta actualiza
`respuestas` de la raíz y el `c` de la tarjeta; ningún bucle de invocaciones
(revisar que la guardia corta). `node --check` de todos los .js tocados.

## Paso 5 — Cliente: hilos (`static/social.js`)

Mantener la filosofía de isla progresiva y el render **exclusivamente** con
`createElement`/`textContent` (UGC: prohibido `innerHTML`).

**5a. Query de raíces.** `cargarComentarios` añade `where('parent', '==', null)` a
las dos queries (primera página y `startAfter`). El primer uso fallará con
`failed-precondition` hasta crear el índice (Paso 8); el cliente ya trata ese fallo
como «sin datos» — mantenerlo.

**5b. Render de rama.** `renderComentario` pasa a crear la raíz + una zona de rama:
- Si `respuestas > 0`: botón `social_replies_show` («Ver respuestas (n)») que al
  primer clic query las respuestas (`parent == id && oculto == false`, `ts asc`,
  páginas de 20 con «Ver más» propio) y las pinta indentadas dentro de un
  contenedor `.comentario-respuestas`; toggle a `social_replies_hide`.
- Botón «Responder» en cada comentario visible (raíz y respuesta). En una respuesta,
  apunta a la raíz de su rama (1 nivel). Solo visible con sesión verificada (mismo
  gate que el form principal); sin sesión → `abrirAuth('comment')`.
- Form de respuesta inline al fondo de la rama (mismo `writeBatch` que el form
  principal — comentario + `lastCommentAt` — pero con `parent: <raízId>`), textarea
  `maxlength=500` con contador, render optimista al final de la rama,
  `respuestas` local +1, contador de la tarjeta `c` +1 con `holdHasta` (las
  respuestas cuentan en el puntaje, decisión del autor).
- **El form principal de la tarjeta añade `parent: null`** al doc (obligatorio por
  reglas).

**5c. Borrado.** Al pulsar «Eliminar» de un comentario propio:
- Respuesta, o raíz con `respuestas == 0` → `deleteDoc` como hoy (+ `c` −1 local, y
  si era respuesta, `respuestas` local −1 en su raíz).
- Raíz con `respuestas > 0` → `updateDoc(ref, { eliminado: true, texto: '',
  autor: '' })` y re-render in situ como placeholder (`social_comment_deleted`, sin
  acciones, sin votos, la rama sigue visible). `c` −1 local con hold (el placeholder
  deja de contar en el agregado).
- Los placeholders que lleguen de Firestore (`eliminado == true`) se pintan igual:
  texto `social_comment_deleted` en itálica, sin autor, sin botones de voto ni
  reporte ni responder.

## Paso 6 — Cliente: votos en comentarios (`static/social.js`)

**6a. UI.** En la fila `.comentario-meta` (o `-acciones`, a criterio del Carril D):
botones like/dislike compactos con contador (`l`/`d` del doc; ausente = 0),
`aria-pressed` para el voto propio y `aria-label` con `social_comment_like_aria`/
`social_comment_dislike_aria`. Sin reading-gate (el gate es de tarjetas). Sin
sesión → `abrirAuth('vote')`. NO exigir correo verificado.

**6b. Mis votos.** Mapa `miVotoComentario` (Map commentId→±1). Al abrir un panel por
primera vez con sesión (y al cambiar de sesión): una query
`commentVotes where uid == me && card == cardId` pinta el estado presionado de todos
los comentarios cargados de esa tarjeta. Cachear por tarjeta para no repetir la
query en la misma sesión de página.

**6c. Votar.** Calcado de `votar()` de tarjetas: toggle (mismo valor → borrar el
doc), optimista con rollback, `enVuelo` por commentId, doc
`commentVotes/<commentId>_<uid>` con `{comment, card, uid, value, ts:
serverTimestamp()}`. Error → restaurar y `toast(panel,
TXT.social_comment_vote_error)`. Los conteos `l/d` pintados se ajustan localmente
(la función confirmará en el doc en la próxima carga; no hace falta hold por
comentario).

**6d. A11y.** Los botones de rama («Ver respuestas», «Responder») con
`aria-expanded`; foco al textarea de respuesta al abrirlo; todo operable por teclado.

## Paso 7 — Diseño visual (Carril D — con skill `frontend-design`)

**Invocar la skill `frontend-design` antes de escribir CSS** (precedente: Paso 7 de
Fase 2). Verificar con build local (`python -m sibylla.cli --html` +
`python -m http.server 8000 --directory web`) y screenshots en 375/768/1280 px.

Alcance: el CSS del bloque de comentarios del template (~L433-463) crece para
cubrir, respetando la estética grecorromana/sci-fi existente (Cinzel para autores,
dorado `rgba(217,184,95,…)`, tinte cian de los paneles):
- **Rama:** indentación de respuestas con un filete lateral sutil (no escaleras de
  cajas); jerarquía tipográfica clara raíz/respuesta; en 375 px la indentación no
  puede comerse el ancho del texto (máx. ~16-20 px + filete).
- **Votos de comentario:** botones fantasma compactos, mismo lenguaje que
  `.soc-btn` pero a escala de texto; estado presionado dorado; contadores discretos.
- **Placeholder eliminado:** itálica apagada, sin acciones — se nota que hubo algo,
  sin ruido.
- **Botones de rama** («Ver respuestas (n)» / «Responder»): estilo de
  `.comentarios-more` existente; que la rama abierta no «grite».
- Respetar `prefers-reduced-motion` en cualquier transición de expandir/colapsar.

No cambiar la estructura DOM/clases que fijó el Carril C sin coordinarlo (los
selectores de `social.js` dependen de ellas).

## Paso 8 — Consola Firebase y despliegue (manual, autor)

En orden:
1. `firebase deploy --only functions` (función extendida, Paso 4).
2. `node functions/backfill-hilos.js` (una vez; requiere
   `gcloud auth application-default login` — recordar que en este entorno gcloud/
   firebase login van por PowerShell, no Git Bash).
3. Publicar `firestore.rules` v3 en la consola (solo tras pasar los tests del
   Paso 3 en el emulador — `getAfter`/ramas `||` son la parte frágil).
4. Desplegar el sitio (cliente nuevo). Abrir sibylla.cl con DevTools: las queries
   nuevas fallarán con `failed-precondition` — seguir los **enlaces del error** para
   crear los índices (`comments(card, oculto, parent, ts DESC)`,
   `comments(parent, oculto, ts ASC)` y, si lo pide, `commentVotes(uid, card)`); si
   una aggregation de la función pide índice, el enlace está en sus logs.
5. Vigilar Usage y las alertas de presupuesto la primera semana (más invocaciones
   por los votos de comentarios; sigue siendo despreciable frente a 2M/mes).

Rollback barato: las reglas v2 siguen siendo válidas (el cliente nuevo recibiría
`permission-denied` al responder/votar comentarios y debe degradar sin romper:
toast de error y nada más).

## Paso 9 — Tests (Carril E)

1. `pytest` completo — debe seguir verde (no se tocó Python; si algo rompió, un
   carril se salió de su alcance).
2. `node --check static/social.js`, `functions/index.js`, `functions/seed.js`,
   `functions/backfill-hilos.js`.
3. Tests de reglas del Paso 3 en el emulador: TODOS los casos nuevos + los de
   Fase 2 sin regresión.
4. Emulador de funciones (Paso 4): escenario completo — comentar, responder, votar
   comentario, reportar respuesta ×3, borrar raíz con respuestas — y verificar
   `agregados/conteos`, `l/d`, `respuestas` coherentes y sin bucles de invocación.
5. Build: `python -m sibylla.cli --html` → HTML con las claves nuevas en
   `#social-i18n`, `web/social.js` copiado.

## Paso 10 — Verificación manual E2E (local + Firestore real)

1. **Hilos:** comentar raíz → responder desde otra cuenta → «Ver respuestas (1)» en
   ambas sesiones; responder a la respuesta cae en la misma rama (no anida); orden:
   raíces nuevas arriba, respuestas viejas arriba dentro de la rama; paginación de
   ambas listas con >20 elementos (puede sembrarse con el emulador o a mano).
2. **Votos de comentarios:** votar/desvotar/cambiar voto → contadores optimistas y,
   tras recargar, los `l/d` del doc coinciden; segunda cuenta ve los totales; el
   voto propio aparece presionado tras recargar (query de mis votos). Cuenta email
   SIN verificar puede votar comentarios pero NO comentar.
3. **Borrado:** raíz sin respuestas → desaparece; raíz con respuestas → placeholder
   «Comentario eliminado» y la rama sobrevive; la respuesta propia se borra de
   verdad; los contadores de la tarjeta bajan en cada caso (verificar
   `agregados/conteos` en consola).
4. **Moderación:** 3 reportes ocultan una respuesta → desaparece y `respuestas` de
   la raíz baja; el flujo de reporte de raíces sigue igual.
5. **XSS:** responder con `<img src=x onerror=alert(1)>` → texto literal.
6. **Puntaje:** una tarjeta con 2 respuestas nuevas sube su `c` en 2 y el orden
   social lo refleja en la próxima carga/build.
7. **Degradación:** bloquear gstatic → sitio estático intacto; reglas v2 en el
   emulador contra cliente v3 → toasts de error, sin excepciones sin capturar.
8. **Móvil 375 px:** rama indentada legible, botones alcanzables, panel sin
   scroll horizontal.

## Riesgos y notas

- **Bucle de la función:** escribir `l/d/respuestas` en `comments` re-dispara
  `onCommentWritten`. La guardia del Paso 4b es OBLIGATORIA y se prueba en el
  emulador (sin ella, cada voto de comentario costaría invocaciones extra en
  cascada).
- **Migración `parent`:** si el cliente v3 sale antes del backfill, los comentarios
  viejos «desaparecen» de los paneles (no matchean `parent == null`). El orden del
  Paso 8 no es negociable.
- **Carrera respuestas/borrado:** una respuesta creada en la ventana en que la raíz
  aún tiene `respuestas: 0` permite un delete real de la raíz → respuesta huérfana
  invisible; la limpieza 4b.3 la borra. Aceptado.
- **Contención de escritura por comentario:** votos simultáneos al mismo comentario
  hacen reintentar a la función (~1 write/s por doc). Teórico a esta escala; salida:
  debounce en la función o agregados por comentario aparte.
- **Costo:** lecturas nuevas = 1 query de «mis votos» por panel abierto + ramas bajo
  demanda; escrituras nuevas = votos de comentarios (cada una ~2 reads de
  aggregation + 1 write de función). Todo dentro del free tier con márgenes de
  órdenes de magnitud; las alertas de $1/$5 ya existen.
- **Privacidad (Ley 21.719):** el placeholder elimina `autor` y `texto` (datos
  personales) del doc; los `commentVotes` del placeholder se limpian solo si el doc
  se borra del todo (4b.4). La eliminación de cuenta completa sigue pendiente de la
  fase de privacidad (anotado desde Fase 2).
