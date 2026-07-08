// Seed inicial de agregados/conteos.
//
// Uso local tras `firebase deploy --only functions`:
//   gcloud auth application-default login
//   npm --prefix functions run seed

const { initializeApp, applicationDefault } = require('firebase-admin/app');
const { getFirestore, AggregateField } = require('firebase-admin/firestore');

initializeApp({ credential: applicationDefault(), projectId: 'sibylla-a81d2' });
const db = getFirestore();

async function countVotes(card) {
  const snap = await db.collection('votes')
    .where('card', '==', card)
    .aggregate({ total: AggregateField.count(), suma: AggregateField.sum('value') })
    .get();
  const data = snap.data() || {};
  const total = Number(data.total || 0);
  const suma = Number(data.suma || 0);
  return {
    l: Math.max(0, Math.round((total + suma) / 2)),
    d: Math.max(0, Math.round((total - suma) / 2)),
  };
}

async function countComments(card) {
  const visibles = await db.collection('comments')
    .where('card', '==', card)
    .where('oculto', '==', false)
    .count()
    .get();
  const eliminados = await db.collection('comments')
    .where('card', '==', card)
    .where('oculto', '==', false)
    .where('eliminado', '==', true)
    .count()
    .get();
  return Math.max(0,
    Number((visibles.data() || {}).count || 0) -
    Number((eliminados.data() || {}).count || 0));
}

async function collectCards(collection) {
  const out = new Set();
  const snap = await db.collection(collection).select('card').get();
  snap.forEach((doc) => {
    const card = doc.get('card');
    if (typeof card === 'string' && card) out.add(card);
  });
  return out;
}

async function main() {
  const cards = new Set([...(await collectCards('votes')), ...(await collectCards('comments'))]);
  const payload = {};
  for (const card of cards) {
    const votos = await countVotes(card);
    payload[card] = { l: votos.l, d: votos.d, c: await countComments(card) };
  }
  if (Object.keys(payload).length) {
    await db.collection('agregados').doc('conteos').set(payload, { merge: true });
  }
  console.log(`Seed de conteos completado: ${Object.keys(payload).length} tarjetas.`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
