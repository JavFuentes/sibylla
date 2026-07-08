# Functions de Sibylla

Mantiene `agregados/conteos` con `{cardId: {l, d, c}}` para que el sitio lea un
solo documento por visita y el build pueda hornear el orden social inicial.

## Deploy manual

```bash
npm --prefix functions install
firebase deploy --only functions
```

## Seed inicial

Los votos existentes de Fase 1 no disparan los triggers hasta que alguien vuelve
a votar. Tras el primer deploy:

```bash
gcloud auth application-default login
npm --prefix functions run seed
```

Si un contador deriva, volver a correr el seed reconstruye `agregados/conteos`.

## Backfill de hilos

`backfill-hilos.js` es una migración one-shot de la Fase social 3. Debe correrse una vez después de desplegar la función actualizada y antes de publicar el cliente nuevo:

```bash
gcloud auth application-default login
node functions/backfill-hilos.js
```

La migración añade `parent: null` a comentarios antiguos e inicializa `l`, `d` y `respuestas` cuando faltan.
