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
