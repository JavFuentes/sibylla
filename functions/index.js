// Cloud Functions de Sibylla: mantiene conteos sociales pre-agregados.
//
// Diseño idempotente: ante cada escritura/borrado de voto o comentario se
// recalcula la tarjeta completa y se escribe en agregados/conteos. Functions es
// at-least-once; recalcular evita deriva por incrementos duplicados.

const { onDocumentWritten } = require('firebase-functions/v2/firestore');
const { initializeApp } = require('firebase-admin/app');
const { getFirestore, AggregateField } = require('firebase-admin/firestore');

initializeApp();
const db = getFirestore();

function cardFromEvent(event) {
  const after = event.data && event.data.after && event.data.after.exists ? event.data.after.data() : null;
  const before = event.data && event.data.before && event.data.before.exists ? event.data.before.data() : null;
  return (after && after.card) || (before && before.card) || null;
}

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
  const snap = await db.collection('comments')
    .where('card', '==', card)
    .where('oculto', '==', false)
    .count()
    .get();
  return Number((snap.data() || {}).count || 0);
}

async function recomputeCard(card) {
  if (!card || typeof card !== 'string') return;
  const votos = await countVotes(card);
  const comentarios = await countComments(card);
  await db.collection('agregados').doc('conteos').set({
    [card]: { l: votos.l, d: votos.d, c: comentarios },
  }, { merge: true });
}

exports.onVoteWritten = onDocumentWritten('votes/{voteId}', async (event) => {
  await recomputeCard(cardFromEvent(event));
});

exports.onCommentWritten = onDocumentWritten('comments/{commentId}', async (event) => {
  await recomputeCard(cardFromEvent(event));
});
