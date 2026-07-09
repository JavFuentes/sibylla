const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');
const {
  initializeTestEnvironment,
  assertFails,
  assertSucceeds,
} = require('@firebase/rules-unit-testing');
const {
  doc,
  setDoc,
  updateDoc,
  deleteDoc,
  getDoc,
  getDocs,
  query,
  collection,
  where,
  orderBy,
  serverTimestamp,
  writeBatch,
  runTransaction,
} = require('firebase/firestore');

let env;

test.before(async () => {
  env = await initializeTestEnvironment({
    projectId: 'sibylla-rules-test',
    firestore: {
      rules: fs.readFileSync(path.resolve(__dirname, '../../firestore.rules'), 'utf8'),
    },
  });
});

test.after(async () => {
  await env.cleanup();
});

test.beforeEach(async () => {
  await env.clearFirestore();
});

function authed(uid, verified = true, name = 'Alicia') {
  return env.authenticatedContext(uid, { email_verified: verified, name }).firestore();
}

// Índice de día UTC como entero YYYYMMDD. espeja diaUtc() de las reglas
// (request.time.year()*10000 + month()*100 + day(), en UTC). Coincide con
// getUTCFullYear()*10000 + (getUTCMonth()+1)*100 + getUTCDate() mientras la
// corrida no cruce medianoche UTC.
function diaUtc() {
  const d = new Date();
  return d.getUTCFullYear() * 10000 + (d.getUTCMonth() + 1) * 100 + d.getUTCDate();
}

async function seedComment(id = 'c1', data = {}) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'comments', id), {
      card: 'n-card', uid: 'author', autor: 'Autora', texto: 'hola',
      ts: new Date(), reportes: 0, oculto: false, parent: null, ...data,
    });
  });
}

// Siembra users/{uid} directamente (sin reglas). Para simular un contador
// diario previo o marcas de tiempo viejas (rate-limit).
async function seedUser(uid, data = {}) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'users', uid), {
      comentariosDia: diaUtc(), comentariosHoy: 0, ...data,
    }, { merge: true });
  });
}

async function seedLastVoteAt(uid, date) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'users', uid), { lastVoteAt: date }, { merge: true });
  });
}

// Crea un comentario con el batch que exigen las reglas: comentario +
// users/{uid} con lastCommentAt + contador diario (comentariosDia/Hoy). dia/hoy
// por defecto reflejan un primer comentario del día; los tests que encadenen
// varios del mismo usuario pasan valores coherentes con el estado sembrado.
async function createComment(db, opts = {}) {
  const {
    id = 'c1', uid = 'alice', autor = 'Alicia', parent = null,
    texto = 'comentario', dia = diaUtc(), hoy = 1, extra = {},
  } = opts;
  const batch = writeBatch(db);
  batch.set(doc(db, 'comments', id), {
    card: 'n-card', uid, autor, texto, parent,
    ts: serverTimestamp(), reportes: 0, oculto: false, ...extra,
  });
  batch.set(doc(db, 'users', uid), {
    lastCommentAt: serverTimestamp(),
    comentariosDia: dia,
    comentariosHoy: hoy,
  }, { merge: true });
  await batch.commit();
}

// Voto de comentario en batch con users/{uid}.lastVoteAt (cooldown de 5 s).
async function createVote(db, opts = {}) {
  const { commentId = 'c1', uid = 'alice', value = 1, extra = {} } = opts;
  const batch = writeBatch(db);
  batch.set(doc(db, 'commentVotes', `${commentId}_${uid}`), {
    comment: commentId, card: 'n-card', uid, value, ts: serverTimestamp(), ...extra,
  });
  batch.set(doc(db, 'users', uid), { lastVoteAt: serverTimestamp() }, { merge: true });
  await batch.commit();
}

// Voto de tarjeta en batch con users/{uid}.lastVoteAt (cooldown de 5 s).
async function createCardVote(db, opts = {}) {
  const { cardId = 'n-card', uid = 'alice', value = 1, extra = {} } = opts;
  const batch = writeBatch(db);
  batch.set(doc(db, 'votes', `${cardId}_${uid}`), {
    card: cardId, uid, value, ts: serverTimestamp(), ...extra,
  });
  batch.set(doc(db, 'users', uid), { lastVoteAt: serverTimestamp() }, { merge: true });
  await batch.commit();
}

test('crear comentario sin verificar falla', async () => {
  const db = authed('alice', false, 'Alicia');
  await assertFails(createComment(db));
});

test('crear comentario verificado funciona', async () => {
  const db = authed('alice', true, 'Alicia');
  await assertSucceeds(createComment(db));
});

test('crear comentario sin parent falla', async () => {
  const db = authed('alice', true, 'Alicia');
  const batch = writeBatch(db);
  batch.set(doc(db, 'comments', 'c1'), {
    card: 'n-card', uid: 'alice', autor: 'Alicia', texto: 'comentario',
    ts: serverTimestamp(), reportes: 0, oculto: false,
  });
  batch.set(doc(db, 'users', 'alice'), {
    lastCommentAt: serverTimestamp(), comentariosDia: diaUtc(), comentariosHoy: 1,
  }, { merge: true });
  await assertFails(batch.commit());
});

test('dos comentarios en menos de 30 segundos falla el segundo', async () => {
  const db = authed('alice', true, 'Alicia');
  await createComment(db, { id: 'c1', hoy: 1 });
  await assertFails(createComment(db, { id: 'c2', hoy: 2 }));
});

test('leer comentarios ocultos falla', async () => {
  await seedComment('c1', { oculto: true });
  const db = authed('reader');
  await assertFails(getDoc(doc(db, 'comments', 'c1')));
});

test('query de comentarios raiz visibles funciona', async () => {
  await seedComment('c1');
  const db = authed('reader');
  await assertSucceeds(getDocs(query(
    collection(db, 'comments'),
    where('card', '==', 'n-card'),
    where('oculto', '==', false),
    where('parent', '==', null),
    orderBy('ts', 'desc'),
  )));
});

test('responder a una raiz visible funciona', async () => {
  await seedComment('root');
  const db = authed('alice', true, 'Alicia');
  await assertSucceeds(createComment(db, { id: 'reply', parent: 'root' }));
});

test('responder a una respuesta falla', async () => {
  await seedComment('root');
  await seedComment('reply', { parent: 'root', uid: 'bob' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, { id: 'nested', parent: 'reply' }));
});

test('responder a padre inexistente falla', async () => {
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, { id: 'reply', parent: 'missing' }));
});

test('responder con card distinta a la raiz falla', async () => {
  await seedComment('root', { card: 'n-other' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, { id: 'reply', parent: 'root' }));
});

test('responder a padre oculto o eliminado falla', async () => {
  await seedComment('hidden', { oculto: true });
  await seedComment('deleted', { eliminado: true, autor: '', texto: '' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, { id: 'r1', parent: 'hidden' }));
  await assertFails(createComment(db, { id: 'r2', parent: 'deleted' }));
});

test('editar texto de comentario falla', async () => {
  await seedComment('c1');
  const db = authed('reporter');
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { texto: 'editado' }));
});

test('autor puede convertir una raiz en placeholder', async () => {
  await seedComment('c1');
  const db = authed('author', true, 'Autora');
  await assertSucceeds(updateDoc(doc(db, 'comments', 'c1'), { eliminado: true, texto: '', autor: '' }));
});

test('otro usuario no puede convertir a placeholder', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { eliminado: true, texto: '', autor: '' }));
});

test('placeholder con texto o claves extra falla', async () => {
  await seedComment('c1');
  const db = authed('author', true, 'Autora');
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { eliminado: true, texto: 'queda', autor: '' }));
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { eliminado: true, texto: '', autor: '', oculto: true }));
});

test('delete real del dueño funciona sin respuestas y falla con respuestas', async () => {
  await seedComment('sin-respuestas');
  await seedComment('con-respuestas', { respuestas: 2 });
  const db = authed('author', true, 'Autora');
  await assertSucceeds(deleteDoc(doc(db, 'comments', 'sin-respuestas')));
  await assertFails(deleteDoc(doc(db, 'comments', 'con-respuestas')));
});

test('autor no puede reportarse', async () => {
  await seedComment('c1');
  const db = authed('author', true, 'Autora');
  await assertFails(runTransaction(db, async (tx) => {
    const cref = doc(db, 'comments', 'c1');
    tx.update(cref, { reportes: 1, oculto: false });
    tx.set(doc(db, 'reports', 'c1_author'), { comment: 'c1', uid: 'author', ts: serverTimestamp() });
    tx.set(doc(db, 'users', 'author'), { lastReportAt: serverTimestamp() }, { merge: true });
  }));
});

test('reportar sin crear doc report falla', async () => {
  await seedComment('c1');
  const db = authed('reporter');
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { reportes: 1, oculto: false }));
});

test('tercer reporte oculta comentario', async () => {
  await seedComment('c1', { reportes: 2 });
  const db = authed('reporter');
  await assertSucceeds(runTransaction(db, async (tx) => {
    const cref = doc(db, 'comments', 'c1');
    tx.update(cref, { reportes: 3, oculto: true });
    tx.set(doc(db, 'reports', 'c1_reporter'), { comment: 'c1', uid: 'reporter', ts: serverTimestamp() });
    tx.set(doc(db, 'users', 'reporter'), { lastReportAt: serverTimestamp() }, { merge: true });
  }));
  await env.withSecurityRulesDisabled(async (ctx) => {
    const snap = await getDoc(doc(ctx.firestore(), 'comments', 'c1'));
    assert.equal(snap.data().oculto, true);
  });
});

test('reportar dos veces falla el segundo', async () => {
  await seedComment('c1');
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'reports', 'c1_reporter'), { comment: 'c1', uid: 'reporter', ts: new Date() });
  });
  const db = authed('reporter');
  await assertFails(runTransaction(db, async (tx) => {
    const cref = doc(db, 'comments', 'c1');
    tx.update(cref, { reportes: 1, oculto: false });
    tx.set(doc(db, 'reports', 'c1_reporter'), { comment: 'c1', uid: 'reporter', ts: serverTimestamp() });
    tx.set(doc(db, 'users', 'reporter'), { lastReportAt: serverTimestamp() }, { merge: true });
  }));
});

test('votar comentario funciona sin correo verificado', async () => {
  await seedComment('c1');
  const db = authed('alice', false, 'Alicia');
  await assertSucceeds(createVote(db, { commentId: 'c1', uid: 'alice' }));
});

test('votar comentario exige id propio y value valido', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await assertFails(setDoc(doc(db, 'commentVotes', 'c1_bob'), {
    comment: 'c1', card: 'n-card', uid: 'alice', value: 1, ts: serverTimestamp(),
  }));
  await assertFails(createVote(db, { commentId: 'c1', uid: 'alice', value: 2 }));
});

test('votar comentario inexistente o de otra card falla', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await assertFails(createVote(db, { commentId: 'missing', uid: 'alice' }));
  await assertFails(createVote(db, { commentId: 'c1', uid: 'alice', extra: { card: 'n-other' } }));
});

test('actualizar voto de comentario a >5 s funciona y borrar siempre funciona', async () => {
  await seedComment('c1');
  // Siembra un voto previo y lastVoteAt hace 6 s (fuera del cooldown).
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'commentVotes', 'c1_alice'), {
      comment: 'c1', card: 'n-card', uid: 'alice', value: 1, ts: new Date(Date.now() - 6000),
    });
  });
  await seedLastVoteAt('alice', new Date(Date.now() - 6000));
  const db = authed('alice', true, 'Alicia');
  // Cambio de voto en batch (con lastVoteAt) a >5 s del último: pasa.
  const b1 = writeBatch(db);
  b1.update(doc(db, 'commentVotes', 'c1_alice'), { value: -1, ts: serverTimestamp() });
  b1.set(doc(db, 'users', 'alice'), { lastVoteAt: serverTimestamp() }, { merge: true });
  await assertSucceeds(b1.commit());
  // Cambio inmediato (<5 s): el cooldown lo frena.
  const b2 = writeBatch(db);
  b2.update(doc(db, 'commentVotes', 'c1_alice'), { value: 1, ts: serverTimestamp() });
  b2.set(doc(db, 'users', 'alice'), { lastVoteAt: serverTimestamp() }, { merge: true });
  await assertFails(b2.commit());
  // Borrar el voto no tiene gate: siempre pasa.
  await assertSucceeds(deleteDoc(doc(db, 'commentVotes', 'c1_alice')));
});

// ---------------------------------------------------------------------------
// Casos nuevos del plan (§A4)
// ---------------------------------------------------------------------------

test('comentario de 241 caracteres falla y 240 pasa', async () => {
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, { id: 'c240', texto: 'a'.repeat(241) }));
  await assertSucceeds(createComment(db, { id: 'c241', texto: 'a'.repeat(240) }));
});

test('sexto comentario del mismo dia UTC falla (tope 5)', async () => {
  const db = authed('alice', true, 'Alicia');
  // Usuario con 5 comentarios hoy y lastCommentAt viejo (no choca con 30 s).
  await seedUser('alice', {
    comentariosDia: diaUtc(), comentariosHoy: 5,
    lastCommentAt: new Date(Date.now() - 60000),
  });
  await assertFails(createComment(db, { id: 'sexto', hoy: 6 }));
});

test('comentario con reinicio de dia (ayer -> hoy) pasa', async () => {
  const db = authed('alice', true, 'Alicia');
  // Usuario con contador de ayer: hoy arranca en 1 (reinicio legítimo).
  await seedUser('alice', {
    comentariosDia: diaUtc() - 1, comentariosHoy: 5,
    lastCommentAt: new Date(Date.now() - 60000),
  });
  await assertSucceeds(createComment(db, { id: 'primero', dia: diaUtc(), hoy: 1 }));
});

test('comentario sin actualizar el contador en users falla', async () => {
  const db = authed('alice', true, 'Alicia');
  const batch = writeBatch(db);
  batch.set(doc(db, 'comments', 'c1'), {
    card: 'n-card', uid: 'alice', autor: 'Alicia', texto: 'comentario', parent: null,
    ts: serverTimestamp(), reportes: 0, oculto: false,
  });
  // users merge SIN comentariosDia/comentariosHoy: el gate del comments create
  // (getAfter(users).comentariosDia == diaUtc()) no se satisface.
  batch.set(doc(db, 'users', 'alice'), { lastCommentAt: serverTimestamp() }, { merge: true });
  await assertFails(batch.commit());
});

test('update suelto de users bajando comentariosHoy el mismo dia falla', async () => {
  await seedUser('alice', { comentariosDia: diaUtc(), comentariosHoy: 3 });
  const db = authed('alice', true, 'Alicia');
  // Intentar resetear a 1 (mismo día) incluso acompañando lastCommentAt: la
  // regla exige incremento (4) o reinicio (que requiere día distinto).
  await assertFails(setDoc(doc(db, 'users', 'alice'), {
    comentariosDia: diaUtc(), comentariosHoy: 1, lastCommentAt: serverTimestamp(),
  }, { merge: true }));
});

test('voto de tarjeta sin batch de users.lastVoteAt falla', async () => {
  const db = authed('alice', true, 'Alicia');
  await assertFails(setDoc(doc(db, 'votes', 'n-card_alice'), {
    card: 'n-card', uid: 'alice', value: 1, ts: serverTimestamp(),
  }));
});

test('voto de tarjeta a <5 s falla y a >5 s pasa; delete sin gate pasa', async () => {
  const db = authed('alice', true, 'Alicia');
  // lastVoteAt hace 2 s: cooldown bloquea el nuevo voto.
  await seedLastVoteAt('alice', new Date(Date.now() - 2000));
  await assertFails(createCardVote(db, { uid: 'alice' }));
  // lastVoteAt hace 6 s: el voto pasa.
  await seedLastVoteAt('alice', new Date(Date.now() - 6000));
  await assertSucceeds(createCardVote(db, { uid: 'alice' }));
  // Borrar el voto no tiene gate.
  await assertSucceeds(deleteDoc(doc(db, 'votes', 'n-card_alice')));
});
