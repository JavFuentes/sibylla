// Cloud Functions de Sibylla: mantiene conteos sociales pre-agregados.
//
// Diseño idempotente: ante cada escritura/borrado de voto o comentario se
// recalcula la tarjeta completa y se escribe en agregados/conteos. Functions es
// at-least-once; recalcular evita deriva por incrementos duplicados.

const { onDocumentWritten } = require('firebase-functions/v2/firestore');
const { setGlobalOptions } = require('firebase-functions/v2/options');
const { initializeApp } = require('firebase-admin/app');
const { getFirestore, AggregateField } = require('firebase-admin/firestore');

// Cinturones anti-runaway de facturación: región fija (Chile) y tope de
// instancias concurrentes. Las alertas de presupuesto ($1/$5) ya existen; esto
// acota el pico de una tormenta de escrituras sobre votos/comentarios.
// OJO: cambiar `region` reubica las funciones en el siguiente deploy ( Firestore
// entrega el evento a cualquier región; funcionalmente sigue igual).
setGlobalOptions({ region: 'southamerica-west1', maxInstances: 5 });

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

async function countCommentVotes(comment) {
  const snap = await db.collection('commentVotes')
    .where('comment', '==', comment)
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

async function countReplies(parent) {
  const visibles = await db.collection('comments')
    .where('parent', '==', parent)
    .where('oculto', '==', false)
    .count()
    .get();
  const eliminados = await db.collection('comments')
    .where('parent', '==', parent)
    .where('oculto', '==', false)
    .where('eliminado', '==', true)
    .count()
    .get();
  return Math.max(0,
    Number((visibles.data() || {}).count || 0) -
    Number((eliminados.data() || {}).count || 0));
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

async function updateCommentVoteCounts(comment) {
  if (!comment || typeof comment !== 'string') return;
  const votos = await countCommentVotes(comment);
  try {
    await db.collection('comments').doc(comment).update({ l: votos.l, d: votos.d });
  } catch (err) {
    if (err && err.code !== 5) throw err; // NOT_FOUND: comentario borrado.
  }
}

exports.onCommentVoteWritten = onDocumentWritten('commentVotes/{voteId}', async (event) => {
  const after = event.data && event.data.after && event.data.after.exists ? event.data.after.data() : null;
  const before = event.data && event.data.before && event.data.before.exists ? event.data.before.data() : null;
  await updateCommentVoteCounts((after && after.comment) || (before && before.comment));
});

function sameValue(a, b) {
  if (a === b) return true;
  if (a && typeof a.isEqual === 'function') return a.isEqual(b);
  if (b && typeof b.isEqual === 'function') return b.isEqual(a);
  return JSON.stringify(a) === JSON.stringify(b);
}

function onlyDenormalizedChanged(before, after) {
  if (!before || !after) return false;
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const changed = [];
  for (const key of keys) {
    if (!sameValue(before[key], after[key])) changed.push(key);
  }
  return changed.length > 0 && changed.every((key) => ['l', 'd', 'respuestas'].includes(key));
}

async function deleteQuery(qs) {
  const snap = await qs.get();
  for (const docSnap of snap.docs) {
    await docSnap.ref.delete();
  }
  return snap.docs;
}

async function deleteCommentVotes(comment) {
  await deleteQuery(db.collection('commentVotes').where('comment', '==', comment));
}

async function updateReplyCount(rootId) {
  if (!rootId || typeof rootId !== 'string') return;
  const n = await countReplies(rootId);
  const ref = db.collection('comments').doc(rootId);
  try {
    await ref.update({ respuestas: n });
    const snap = await ref.get();
    const data = snap.exists ? snap.data() : null;
    if (data && data.eliminado === true && n === 0) {
      await ref.delete();
    }
  } catch (err) {
    if (err && err.code !== 5) throw err;
  }
}

exports.onCommentWritten = onDocumentWritten('comments/{commentId}', async (event) => {
  const before = event.data && event.data.before && event.data.before.exists ? event.data.before.data() : null;
  const after = event.data && event.data.after && event.data.after.exists ? event.data.after.data() : null;
  if (onlyDenormalizedChanged(before, after)) return;

  const commentId = event.params.commentId;
  await recomputeCard(cardFromEvent(event));

  const beforeParent = before && typeof before.parent === 'string' ? before.parent : null;
  const afterParent = after && typeof after.parent === 'string' ? after.parent : null;
  const parents = new Set([beforeParent, afterParent].filter(Boolean));
  for (const parent of parents) await updateReplyCount(parent);

  if (!after) {
    await deleteCommentVotes(commentId);
    if (!beforeParent) {
      const replies = await deleteQuery(db.collection('comments').where('parent', '==', commentId));
      for (const reply of replies) await deleteCommentVotes(reply.id);
    }
  }
});
