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

async function seedComment(id = 'c1', data = {}) {
  await env.withSecurityRulesDisabled(async (ctx) => {
    await setDoc(doc(ctx.firestore(), 'comments', id), {
      card: 'n-card', uid: 'author', autor: 'Autora', texto: 'hola',
      ts: new Date(), reportes: 0, oculto: false, parent: null, ...data,
    });
  });
}

async function createComment(db, id = 'c1', data = {}, uid = 'alice', autor = 'Alicia') {
  const batch = writeBatch(db);
  batch.set(doc(db, 'comments', id), {
    card: 'n-card', uid, autor, texto: 'comentario', parent: null,
    ts: serverTimestamp(), reportes: 0, oculto: false, ...data,
  });
  batch.set(doc(db, 'users', uid), { lastCommentAt: serverTimestamp() }, { merge: true });
  await batch.commit();
}

async function createVote(db, commentId = 'c1', uid = 'alice', data = {}) {
  await setDoc(doc(db, 'commentVotes', `${commentId}_${uid}`), {
    comment: commentId, card: 'n-card', uid, value: 1, ts: serverTimestamp(), ...data,
  });
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
  batch.set(doc(db, 'users', 'alice'), { lastCommentAt: serverTimestamp() }, { merge: true });
  await assertFails(batch.commit());
});

test('dos comentarios en menos de 30 segundos falla el segundo', async () => {
  const db = authed('alice', true, 'Alicia');
  await createComment(db, 'c1');
  await assertFails(createComment(db, 'c2'));
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
  await assertSucceeds(createComment(db, 'reply', { parent: 'root' }));
});

test('responder a una respuesta falla', async () => {
  await seedComment('root');
  await seedComment('reply', { parent: 'root', uid: 'bob' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, 'nested', { parent: 'reply' }));
});

test('responder a padre inexistente falla', async () => {
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, 'reply', { parent: 'missing' }));
});

test('responder con card distinta a la raiz falla', async () => {
  await seedComment('root', { card: 'n-other' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, 'reply', { parent: 'root' }));
});

test('responder a padre oculto o eliminado falla', async () => {
  await seedComment('hidden', { oculto: true });
  await seedComment('deleted', { eliminado: true, autor: '', texto: '' });
  const db = authed('alice', true, 'Alicia');
  await assertFails(createComment(db, 'r1', { parent: 'hidden' }));
  await assertFails(createComment(db, 'r2', { parent: 'deleted' }));
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
  await assertSucceeds(createVote(db, 'c1', 'alice'));
});

test('votar comentario exige id propio y value valido', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await assertFails(setDoc(doc(db, 'commentVotes', 'c1_bob'), {
    comment: 'c1', card: 'n-card', uid: 'alice', value: 1, ts: serverTimestamp(),
  }));
  await assertFails(createVote(db, 'c1', 'alice', { value: 2 }));
});

test('votar comentario inexistente o de otra card falla', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await assertFails(createVote(db, 'missing', 'alice'));
  await assertFails(createVote(db, 'c1', 'alice', { card: 'n-other' }));
});

test('actualizar y borrar voto propio de comentario funciona', async () => {
  await seedComment('c1');
  const db = authed('alice', true, 'Alicia');
  await createVote(db, 'c1', 'alice');
  await assertSucceeds(updateDoc(doc(db, 'commentVotes', 'c1_alice'), { value: -1, ts: serverTimestamp() }));
  await assertSucceeds(deleteDoc(doc(db, 'commentVotes', 'c1_alice')));
});
