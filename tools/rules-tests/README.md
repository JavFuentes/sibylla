# Tests de reglas Firestore

Instalación local:

```bash
npm install
firebase emulators:exec --only firestore "npm test"
```

Cubren los flujos críticos de comentarios/reportes y las suscripciones al
boletín de `firestore.rules` sin tocar el proyecto real.
