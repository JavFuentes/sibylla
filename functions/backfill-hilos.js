// Migración one-shot para Fase social 3.
//
// Uso tras desplegar Functions y antes de publicar el cliente nuevo:
//   gcloud auth application-default login
//   node functions/backfill-hilos.js

const { initializeApp, applicationDefault } = require('firebase-admin/app');
const { getFirestore } = require('firebase-admin/firestore');

initializeApp({ credential: applicationDefault(), projectId: 'sibylla-a81d2' });
const db = getFirestore();

async function main() {
  const snap = await db.collection('comments').get();
  let tocados = 0;
  let batch = db.batch();
  let pending = 0;

  async function flush() {
    if (!pending) return;
    await batch.commit();
    batch = db.batch();
    pending = 0;
  }

  for (const docSnap of snap.docs) {
    const data = docSnap.data() || {};
    const patch = {};
    if (!Object.prototype.hasOwnProperty.call(data, 'parent')) patch.parent = null;
    if (!Object.prototype.hasOwnProperty.call(data, 'l')) patch.l = 0;
    if (!Object.prototype.hasOwnProperty.call(data, 'd')) patch.d = 0;
    const isRoot = !Object.prototype.hasOwnProperty.call(data, 'parent') || data.parent == null;
    if (isRoot && !Object.prototype.hasOwnProperty.call(data, 'respuestas')) patch.respuestas = 0;
    if (!Object.keys(patch).length) continue;
    batch.update(docSnap.ref, patch);
    pending++;
    tocados++;
    if (pending >= 450) await flush();
  }
  await flush();
  console.log(`Backfill de hilos completado: ${tocados} comentarios actualizados.`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
