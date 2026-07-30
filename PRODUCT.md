# Producto

<!-- impeccable:product-schema 1 -->

## Plataforma

web

## Usuarios

El usuario principal es un lector chileno hispanohablante que quiere informarse a diario sobre actualidad nacional, ciencia y tecnología sin depender de portadas ordenadas por clics ni de criterios editoriales opacos. Necesita descubrir qué merece atención, entenderlo rápidamente y llegar siempre a la fuente original.

## Propósito del producto

Sibylla es una lectora periódica de noticias que reúne fuentes curadas, elimina duplicados, ordena con reglas públicas y publica una portada y un boletín en español. Su éxito consiste en reducir el trabajo de elegir qué leer sin ocultar cómo se tomó cada decisión y sin sustituir a los medios o instituciones que originaron la información.

## Posicionamiento

Sibylla convierte su criterio editorial en infraestructura auditable: las fuentes, tiers de confiabilidad, reglas de selección, límites de diversidad, presupuestos y usos de IA están versionados públicamente. Cada tarjeta conserva el vínculo al original y la IA tiene un rol acotado a traducción, resumen y ordenamiento donde corresponde; nunca inventa noticias.

## Contexto de uso

- La portada se regenera automáticamente una vez al día y se consume como una página estática monolingüe en español.
- El lector explora secciones temáticas, ajusta intereses, orden y cantidad de tarjetas, o usa un feed aleatorio; esa personalización permanece en el navegador.
- El boletín diario reutiliza la misma edición editorial de la portada.
- El operador mantiene fuentes, publicaciones propias y canales de divulgación desde archivos versionados y una herramienta administrativa local.

## Capacidades y restricciones

- Pipeline de ingesta con fallo aislado por fuente, normalización, deduplicación, agrupación, ranking y diversificación.
- Tiers de confiabilidad: primaria o peer-review, periodismo y agregación o discusión.
- Sitio monolingüe en español orientado a Chile; la ingesta puede ser multilingüe.
- Generación estática desde `sibylla/templates/index.html.j2`; `web/` es salida generada y no se edita a mano.
- Sin publicidad ni tracking. La personalización ordinaria se guarda en `localStorage`.
- La capa LLM es opcional, se ejecuta durante el build, usa proveedores mediante HTTP directo y degrada de forma segura si no está configurada.
- Los secretos viven en `.env`; los costos externos tienen límites y cachés explícitos.
- Las reglas editoriales y de producto deben permanecer públicas, legibles y versionadas.

## Compromisos de marca

- Nombre: Sibylla.
- Voz: informativa, sobria y transparente; explica sus criterios sin presentarse como autoridad infalible.
- La identidad existente y sus activos oficiales deben preservarse salvo que el usuario solicite explícitamente un rediseño o rebranding.
- Activos canónicos disponibles en `images/` y `static/`; la interfaz vigente tiene su fuente de verdad en `sibylla/templates/index.html.j2` y sus textos en `locales/es.json`.

## Evidencia disponible

- Producto en producción en `https://sibylla.cl`.
- Reglas de selección por sección en `SECCIONES.md` y registro curado de fuentes en `config/sources.yaml`.
- Implementación y arquitectura documentadas en `README.md`, `AGENTS.md` y `docs/codebase-graph.html`.
- Suite de pruebas sin red en `tests/`, con estado documentado en `TEST.md`.
- Captura del producto en `images/screenshot-web.png` y logotipos en `images/` y `static/`.
- No hay testimonios, estudios de usuarios ni métricas de impacto confirmadas; el trabajo futuro no debe fabricarlos.

## Principios de producto

1. La confiabilidad y la trazabilidad pesan más que la popularidad.
2. Las reglas editoriales deben poder inspeccionarse, discutirse y modificarse mediante cambios versionados.
3. Una noticia conduce a su fuente: Sibylla resume y orienta, no republica ni reemplaza el original.
4. La personalización debe aportar control al lector sin convertir su comportamiento en un producto publicitario.
5. Las fuentes, servicios externos y modelos pueden fallar; la experiencia diaria debe degradar de forma aislada y predecible.
