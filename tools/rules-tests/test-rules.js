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
      ts: new Date(), reportes: 0, oculto: false, ...data,
    });
  });
}

async function createComment(db, id = 'c1') {
  const batch = writeBatch(db);
  batch.set(doc(db, 'comments', id), {
    card: 'n-card', uid: 'alice', autor: 'Alicia', texto: 'comentario',
    ts: serverTimestamp(), reportes: 0, oculto: false,
  });
  batch.set(doc(db, 'users', 'alice'), { lastCommentAt: serverTimestamp() }, { merge: true });
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

test('query de comentarios visibles funciona', async () => {
  await seedComment('c1');
  const db = authed('reader');
  await assertSucceeds(getDocs(query(collection(db, 'comments'), where('oculto', '==', false), orderBy('ts', 'desc'))));
});

test('editar texto de comentario falla', async () => {
  await seedComment('c1');
  const db = authed('reporter');
  await assertFails(updateDoc(doc(db, 'comments', 'c1'), { texto: 'editado' }));
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
