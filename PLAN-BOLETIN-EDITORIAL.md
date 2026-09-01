# PLAN-BOLETIN-EDITORIAL.md — Boletín diario «1 noticia + 4 señales»

> **Estado:** revisado por Claude e implementado por Codex el 1 de septiembre de
> 2026 tras autorización explícita del usuario. Verificación: suite completa y
> revisión visual acotada en escritorio y móvil.

## Cambios introducidos en la revisión

1. **La destacada ya no se elige por el orden de los temas.** Ese orden no es una
   preferencia del lector (ver «Selección de la noticia destacada»). Se sustituye por un
   puntaje editorial con rotación determinista.
2. **El resumen de la destacada sí lleva un tope.** El campo `resumen` no siempre
   contiene un resumen: la sección SIBYLLA inyecta por ese canal el cuerpo Markdown
   completo de una publicación propia.
3. **La política «sin resumen, no se envía» queda condicionada a medir antes la
   cobertura real** de resúmenes en una corrida de CI.
4. **Ajustes menores:** arranque del round-robin de las breves, tratamiento explícito de
   la sección fija, breves sin extracto, limpieza de `ver_en_sitio` y prueba de
   inyección CR/LF en el asunto.
5. Las cinco preguntas abiertas quedan resueltas al final del documento.

## Objetivo

Convertir el correo diario de Sibylla desde un índice de titulares a una edición
editorial breve que entregue valor dentro del propio mensaje:

- **1 noticia destacada** con su resumen completo en español.
- Hasta **4 señales secundarias** de los temas elegidos por el lector.
- Sin el bloque genérico «Sibylla observa».
- Sin una llamada LLM exclusiva para redactar la apertura del correo.
- Siempre con enlace visible a la publicación original.

La promesa del formato es: aunque el lector no abra ningún enlace, termina el correo
habiendo entendido al menos una noticia relevante.

## Problema actual

El boletín hoy reutiliza las tarjetas de la portada, pero las presenta como una lista de
hasta 12 entradas. Antes de la lista muestra una síntesis general de 60–90 palabras.

Ese flujo tiene tres problemas:

1. **La apertura no entrega información concreta.** Cuando no hay proveedor LLM o la
   llamada falla, `construir_sintesis()` usa un fallback con métricas del build
   («310 señales en 30 fuentes»). Describe el proceso de Sibylla, no el día noticioso.
2. **Los resúmenes existentes no se aprovechan.** La plantilla usa primero
   `card.snippet` y solo cae a `card.resumen` si no hay snippet. Además corta el texto a
   260 caracteres, por lo que nunca muestra un resumen completo.
3. **La cantidad sustituye a la jerarquía.** `repartir()` distribuye hasta 12 tarjetas,
   pero ninguna aparece como la lectura principal. El resultado exige recorrer muchos
   titulares sin asegurar comprensión.

## Hallazgo técnico clave

Los resúmenes completos en español ya existen antes de construir el boletín:

1. `sibylla/web.py::build_all_sites()` selecciona las tarjetas visibles.
2. `sibylla/resumen.py::build_resumenes()` genera o recupera del caché un resumen en
   español de 3–5 frases por tarjeta.
3. `build_context()` incorpora ese texto como `card["resumen"]`.
4. `edicion_desde_contexto()` ya conserva el campo `resumen` en
   `data/newsletter_edicion.json`.

Por tanto, el nuevo formato puede reutilizar esos resúmenes. No necesita una nueva
llamada al modelo para la noticia destacada.

En cambio, la apertura actual sí provoca una llamada adicional e independiente:
`build_all_sites()` llama a `newsletter.construir_sintesis()`, que ejecuta
`provider.complete()` con el propósito `newsletter_sintesis`. Eliminar esa apertura
ahorra **una llamada LLM por build** sin afectar traducciones ni resúmenes de tarjetas.

## Formato propuesto

```text
SIBYLLA
1 de septiembre de 2026

LA SEÑAL DEL DÍA · FRONTERA DIGITAL

Título de la noticia destacada en español
Fuente · fecha · TII

Resumen completo en español de 3–5 frases. No se trunca y no se sustituye
por el snippet de la fuente.

Leer la publicación original →

EN BREVE

• Título secundario · fuente · tema
• Título secundario · fuente · tema

[Hasta cuatro señales, sin extracto]

Darme de baja · Cambiar mis temas
```

### Jerarquía

- La noticia destacada es el foco inequívoco del correo.
- Su resumen se muestra completo: sin acordeón y sin el límite de 260 caracteres.
- «Completo» significa **el resumen entero tal como lo generó `resumen.py`** (3–5
  frases, del orden de 600–900 caracteres), no un texto arbitrariamente largo. Se aplica
  un tope de seguridad de 1.500 caracteres cortando en frase, nunca en carácter. Ese
  tope no debe activarse jamás con un resumen normal; existe solo para que un texto
  anómalo no rompa el correo (ver riesgo 6).
- Solo ella lleva una llamada a la acción prominente:
  **«Leer la publicación original»**.
- Las señales secundarias sirven para descubrir, no para competir con la destacada. Por
  eso no llevan extracto: repetir un párrafo bajo cada título reconstruye el muro de
  texto que este rediseño elimina.
- Se elimina el enlace «Comentar» repetido en cada tarjeta. Si se conserva, debe quedar
  subordinado a la acción principal de la destacada.

## Selección de la noticia destacada

La composición ocurre durante el envío, después de conocer los temas de cada
suscriptor.

### Por qué no se usa el orden de los temas

La versión anterior de este plan elegía la destacada recorriendo los temas del lector en
el orden en que llegan desde Firestore. **Esa premisa es falsa.** El array `temas` no
expresa una preferencia: en `static/social.js`, `temasIniciales()` lo inicializa con el
orden fijo `nacional, ai, medicine, astronomia, divulgacion` (o con los temas visibles
del sitio en ese mismo orden) y el toggle solo hace `push` al final. `suscriptores.py`
conserva ese orden tal cual.

Consecuencia práctica: casi todos los suscriptores recibirían la primera tarjeta de
Nacional como destacada, todos los días. La sección que encabeza el correo quedaría
decidida por el orden de unos checkboxes.

### Primera opción: destacada personalizada por puntaje

1. Ejecutar la selección temática actual para obtener el pool personalizado.
2. Descartar como candidatas:
   - las tarjetas sin `resumen` no vacío;
   - las tarjetas de vídeo (`is_video`), que nunca tienen resumen;
   - **las tarjetas de la sección `sibylla`**, porque su campo `resumen` no es un
     resumen sino el cuerpo Markdown completo de una publicación propia (ver riesgo 6).
     Siguen siendo elegibles como señal breve.
3. Ordenar las candidatas por:
   1. **sello de confianza** (`seal_roman`: I antes que II antes que III);
   2. **posición dentro de su sección**, que ya viene rankeada por la portada;
   3. desempate estable por `id` de la tarjeta.
4. Aplicar una **rotación determinista de la sección líder** para que el correo no
   quede monopolizado por el mismo tema: entre las candidatas mejor puntuadas se
   prefiere la del tema cuyo índice corresponda a un valor derivado de
   `(fecha de la edición, uid del suscriptor)`.

La rotación debe ser una función pura y determinista de esos dos valores. Es un
requisito, no un detalle: el cron de respaldo reconstruye la edición el mismo día, y un
suscriptor omitido en la primera corrida debe poder recibir una destacada equivalente
en la segunda sin que la elección dependa del azar o del reloj.

Este criterio respeta:

- el ranking editorial real (sello y posición), en vez de un orden accidental;
- la diversidad entre temas a lo largo de los días;
- la idempotencia del envío.

### Segunda opción: respaldo editorial global

Si ninguna tarjeta de los temas elegidos tiene resumen, aplicar el mismo puntaje sobre
la edición global y mostrar la ganadora con la etiqueta **«Selección editorial del
día»**, para no presentarla falsamente como personalización. Rigen las mismas
exclusiones: nada de vídeos y nada de la sección `sibylla`.

Este respaldo cubre especialmente al lector que haya elegido solo **Divulgación**,
porque los videos de YouTube no se resumen actualmente.

### Sin ningún resumen disponible

Si toda la edición carece de resúmenes, no enviar el boletín. Registrar una omisión con
motivo agregado y sin datos personales legibles. No degradar nuevamente a un correo de
titulares ni fabricar un resumen desde un snippet insuficiente.

Principio: **es preferible omitir una edición defectuosa que enviar un correo que no
cumple su promesa.**

#### Requisito previo: medir la cobertura de resúmenes

Esta regla es correcta como principio, pero hoy **nadie sabe qué fracción de las
tarjetas visibles llega con resumen en una corrida real de CI**. Depende de paywalls,
de páginas que exigen JavaScript y de bloqueos en `articles.card_content`. Si la
cobertura fuese baja, la regla convertiría en silencio un correo mediocre en cero
correos.

Por tanto, **antes de implementar la política de no envío**:

1. Añadir al build una línea de log con la cobertura agregada, del tipo
   `resúmenes: N/M tarjetas visibles`. Es una métrica sin datos personales.
2. Observar dos o tres corridas del cron real.
3. Solo entonces decidir si la regla se aplica tal cual, o si conviene un umbral
   distinto (por ejemplo, exigir resumen únicamente para la destacada, que es lo que la
   promesa del formato requiere de verdad).

El paso 1 puede implementarse por separado y antes que el resto del plan: es
independiente, no cambia comportamiento y aporta el dato que falta para decidir.

## Selección de las cuatro señales secundarias

Después de extraer la destacada:

1. Eliminarla del pool para evitar duplicación.
2. Recorrer las secciones personalizadas por round-robin **empezando por el tema
   siguiente al de la destacada**, no por el primero. Con cinco temas y cuatro señales,
   arrancar desde el principio dejaría un tema sin representación en todas las
   ediciones; arrancando después de la destacada, los cinco temas aparecen.
3. Tomar como máximo cuatro tarjetas.
4. **No mostrar extracto.** Cada señal es título, fuente y tema. Al no haber extracto,
   desaparece toda la lógica de truncado del bloque secundario y la regla «nunca
   truncar la destacada» queda trivialmente consistente.

El bloque puede contener menos de cuatro señales si no hay suficientes tarjetas. Si no
queda ninguna, se omite por completo el encabezado «En breve».

### La sección fija SIBYLLA

`repartir()` añade hoy `SECCION_FIJA` («sibylla») al final del orden de temas,
independientemente de lo que haya elegido el suscriptor. Con doce tarjetas eso era
inocuo; con cuatro señales, una publicación propia puede desplazar a una noticia de un
tema efectivamente elegido.

Decisión para esta implementación: **la sección `sibylla` compite por las señales
breves, pero solo después de agotar una ronda completa de los temas del lector.** Nunca
es destacada. Si el resultado no convence en la verificación visual, la alternativa es
excluirla del correo y dejarla como contenido exclusivo del sitio.

## Asunto y preheader

El asunto actual solo contiene la fecha. Se propone hacerlo informativo y específico
para cada destinatario:

```text
Sibylla hoy · <título de la destacada>
```

- Normalizar espacios y saltos de línea del título.
- Acortarlo a una longitud segura para bandejas de entrada, sin cortar palabras cuando
  sea posible.
- Mantener el prefijo `[PRUEBA]` en el modo de prueba.

El preheader será la primera parte del resumen de la destacada, no una descripción del
proceso de Sibylla.

## Cambios previstos por archivo

### `sibylla/newsletter.py`

- Eliminar `construir_sintesis()` y la dependencia de `get_provider`.
- Eliminar la síntesis de `edicion_desde_contexto()`.
- Añadir una función pura de composición, por ejemplo:
  `componer_boletin(secciones_personales, secciones_globales, *, fecha, uid)`.
  Recibe `fecha` y `uid` porque la rotación de la sección líder depende de ambos, y
  debe seguir siendo determinista y testeable sin reloj ni azar.
- Devolver una estructura con:
  - `destacada`;
  - `breves`;
  - indicador de respaldo editorial global.
- Añadir el tope de seguridad del resumen de la destacada (1.500 caracteres, corte en
  frase) en el código y no en la plantilla, para que sea testeable.
- Construir el asunto dentro del bucle por destinatario, después de elegir la
  destacada. Normalizar espacios, **rechazar CR/LF** y acortar antes de construir el
  `EmailMessage`.
- Omitir el envío cuando no exista ningún resumen completo, según lo que decida la
  medición de cobertura descrita más arriba.
- Mantener el aislamiento de fallos, el enmascarado de direcciones y el flush del
  estado tras cada destinatario.

### `sibylla/web.py`

- Dejar de importar y ejecutar `construir_sintesis()`.
- Construir `newsletter_edicion.json` directamente desde el contexto ya resumido.
- Conservar intacta la generación de traducciones y resúmenes de la portada.
- Registrar la cobertura agregada de resúmenes (`resúmenes: N/M tarjetas visibles`).
  Este punto puede adelantarse al resto del plan.

### `sibylla/templates/newsletter.html.j2`

- Eliminar el bloque «Sibylla observa» y `edicion.sintesis`.
- Renderizar la destacada como bloque editorial principal.
- Mostrar `destacada.resumen` completo y escapado por Jinja.
- Añadir el CTA «Leer la publicación original».
- Reemplazar las secciones extensas por un único bloque «En breve» de hasta cuatro
  entradas, **sin extracto**: título, fuente y tema.
- Mantener estilos inline y tablas para compatibilidad con clientes de correo.
- No añadir JavaScript, formularios, imágenes remotas nuevas ni CSS dependiente de
  características modernas.

### `sibylla/templates/newsletter.txt.j2`

- Replicar la misma jerarquía semántica que el HTML.
- Incluir el resumen completo y la URL original en texto plano.
- No incluir la síntesis eliminada.

### `locales/*.json`

- Eliminar las claves que solo alimentaban el prompt y fallback de la síntesis:
  `system_prompt`, `user_prompt`, `sintesis_fallback` y `saludo`.
- Aprovechar para eliminar `ver_en_sitio`, que ya es una clave muerta: no la usa
  ninguna plantilla ni ningún módulo.
- Añadir las claves necesarias para:
  - «La señal del día»;
  - «Selección editorial del día»;
  - «En breve»;
  - «Leer la publicación original».
- Actualizar `subject` para recibir `{title}`.
- Mantener la paridad estructural que exijan los tests actuales.

### `tests/test_newsletter.py`

- Eliminar las pruebas de `construir_sintesis()` y del tracker
  `newsletter_sintesis`.
- Probar que la destacada se elige por sello y posición, **no** por el orden del array
  `temas`: con un lector cuyo primer tema traiga una tarjeta de sello III y un tema
  posterior una de sello I, debe ganar la segunda.
- Probar que la rotación es determinista: mismo `(fecha, uid)` produce siempre la misma
  destacada; distintos `uid` o días producen variedad de secciones.
- Probar que se excluyen como destacada los vídeos y las tarjetas de la sección
  `sibylla`, aunque tengan `resumen`.
- Probar el recorrido cuando las primeras tarjetas no tienen resumen.
- Probar el respaldo editorial global.
- Probar que una edición sin ningún resumen no envía correo.
- Probar que la destacada no se repite entre las señales.
- Probar que las breves arrancan en el tema siguiente al de la destacada y que, con
  cinco temas, aparecen los cinco.
- Probar el límite de cuatro señales y que ninguna lleva extracto.
- Probar que el resumen completo aparece sin truncarse en HTML y texto plano.
- Probar el tope de seguridad: un `resumen` anómalamente largo se corta en frase y no
  se emite entero.
- Probar que no aparecen «Sibylla observa» ni el antiguo fallback.
- Probar asunto y preheader derivados de la destacada.
- Probar que un título con CR/LF no puede inyectar cabeceras en el `Subject`.
- Mantener las pruebas existentes de SMTP, idempotencia, dry-run, aislamiento de
  destinatarios y privacidad de logs.

### Documentación

- Actualizar la sección «Boletín diario por correo» de `AGENTS.md` una vez aprobada e
  implementada la propuesta.
- Documentar el nuevo contrato editorial 1+4 y la política de no envío sin resumen.

## Compatibilidad y migración

La forma más segura es subir la versión del sidecar desde
`cl.sibylla.newsletter_edicion.v1` a `v2`, porque desaparece `sintesis` y cambia el
contrato de composición.

Ventajas del cambio de versión:

- el proceso de envío nuevo rechaza una edición antigua;
- un fallo parcial del deploy no puede enviar accidentalmente el formato anterior;
- los tests hacen explícito el contrato esperado.

El workflow ya construye la edición antes de publicar y enviar, por lo que no debería
ser necesaria una migración de datos históricos. El estado de destinatarios
`newsletter_state.json` conserva su esquema actual.

## Coste esperado

- **Se elimina:** 1 llamada LLM por build (`newsletter_sintesis`).
- **Se reutiliza:** el resumen en español que la web ya genera y cachea.
- **No aumenta:** extracción de artículos, traducciones ni resúmenes por tarjeta.
- **Puede reducirse:** tamaño total del email, al pasar de hasta 12 extractos a 1
  resumen completo + 4 señales breves.

No afirmar un ahorro monetario exacto sin medir tokens reales por proveedor y modelo.

## Riesgos y mitigaciones

### 1. Suscriptor con solo Divulgación

Los videos no tienen resumen. El respaldo editorial global garantiza una noticia
desarrollada, pero introduce contenido fuera de sus temas.

Mitigación: etiquetarlo de forma transparente como selección editorial y mantener sus
cuatro señales personalizadas en Divulgación.

### 2. Extracción fallida en prensa

Una tarjeta puede tener snippet, pero no resumen, por paywall, JavaScript o bloqueo.

Mitigación: recorrer otros candidatos. No convertir el snippet automáticamente en
«resumen completo».

### 3. LLM no disponible durante todo el build

Los aciertos del caché aún pueden sostener la edición. Si no queda ninguno, se omite el
envío y se registra el motivo.

### 4. Asunto demasiado largo o con caracteres extraños

Mitigación: limpiar espacios, prohibir CR/LF y aplicar un límite antes de construir el
`EmailMessage`.

### 5. Estado diario de destinatarios omitidos

No hay que decidir nada nuevo: el comportamiento actual ya es el correcto. `pendientes()`
excluye únicamente a quienes están en `enviados`, y `enviar()` marca `terminado` solo si
no quedan pendientes. Por tanto, un destinatario omitido por falta de resumen mantiene
el día abierto y el cron de respaldo —que reconstruye la edición— vuelve a intentarlo
con material nuevo. No hay reintento infinito: el estado se cierra al cambiar la fecha.

Lo único que debe cuidarse es que la destacada sea determinista por `(fecha, uid)`, para
que ese segundo intento no dependa del azar.

### 6. El campo `resumen` no siempre es un resumen

`_tarjeta()` inyecta por el mismo canal `resumenes` textos que no son resúmenes de 3–5
frases:

- la sección SIBYLLA pasa el **cuerpo Markdown completo** de la publicación propia;
- la tarjeta APOD pasa la explicación traducida de la NASA.

Sin protección, la destacada podría ser un artículo entero en Markdown crudo dentro del
correo: ni escapado como HTML, ni renderizado como Markdown, ni acotado.

Mitigación doble: excluir la sección `sibylla` de las candidatas a destacada y aplicar
igualmente el tope de 1.500 caracteres con corte en frase.

### 7. Monotonía de la sección líder

Aun con puntaje por sello, un tema fuerte puede acaparar la destacada. La rotación
determinista por `(fecha, uid)` es la mitigación; conviene verificarla generando la
composición de varios días seguidos para un mismo `uid` de prueba y comprobando que la
sección líder cambia.

## Verificación visual y funcional

Antes de habilitar el envío real:

1. Renderizar una edición de prueba con textos y titulares largos, incluido un
   `resumen` anómalo (cuerpo Markdown completo) para comprobar el tope de seguridad.
2. Revisar HTML en escritorio y móvil.
3. Revisar al menos Gmail web/móvil y un cliente con modo oscuro.
4. Confirmar que el resumen completo sigue siendo legible y que el CTA no se pierde.
5. Verificar la alternativa `text/plain` del mensaje.
6. Ejecutar `tests/test_newsletter.py`, `tests/test_locales.py` y la suite relacionada
   con web y suscriptores.
7. Ejecutar primero `SIBYLLA_NEWSLETTER_DRYRUN=1`.
8. Enviar después únicamente a `SIBYLLA_NEWSLETTER_TEST_TO`.
9. Confirmar asunto, preheader, enlaces, baja y ausencia del texto antiguo.
10. Componer la edición para un mismo `uid` de prueba en varias fechas seguidas y
    comprobar que la sección líder rota y que la elección es reproducible.
11. Solo entonces habilitar el envío normal.

## Criterios de aceptación

- Ningún correo contiene «Sibylla observa» ni la antigua síntesis genérica.
- No existe una llamada LLM con propósito `newsletter_sintesis`.
- Cada correo enviado contiene exactamente una noticia destacada con resumen completo
  en español.
- La destacada enlaza a la fuente original y no se repite.
- La destacada no depende del orden del array `temas`, y nunca es un vídeo ni una
  publicación propia de la sección `sibylla`.
- La sección líder rota entre días para un mismo suscriptor, de forma determinista.
- Hay como máximo cuatro señales secundarias, sin extracto, arrancando en el tema
  siguiente al de la destacada.
- La cobertura de resúmenes está medida en corridas reales antes de activar la política
  de no envío.
- El asunto y el preheader comunican la noticia destacada.
- Si no existe ningún resumen válido, no se envía un correo degradado.
- La personalización, baja, privacidad de logs, envío individual, reanudación e
  idempotencia siguen funcionando.
- El sitio web y sus resúmenes no cambian como efecto lateral.

## Decisiones resueltas en la revisión

1. **¿Destacada global de respaldo cuando los temas del lector no tienen resumen?**
   Sí, con la etiqueta «Selección editorial del día». Es el caso del suscriptor que solo
   eligió Divulgación. Nunca puede ser una publicación propia.
2. **¿`v2` del sidecar o compatibilidad con `v1`?**
   `v2`, sin período de compatibilidad. `cargar_edicion()` ya rechaza esquemas distintos
   y el workflow construye y envía en la misma corrida, así que no existe ventana en la
   que convivan dos versiones.
3. **¿Reintentar a los omitidos el mismo día?**
   Sí, y ya ocurre con el código actual. No requiere cambios (ver riesgo 5).
4. **¿Titular en el asunto o fecha?**
   Titular, con prefijo estable `Sibylla · `, tope de unos 65 caracteres, sin CR/LF y
   conservando `[PRUEBA]` en modo prueba.
5. **¿Extracto en las señales breves?**
   No: solo título, fuente y tema. Un extracto bajo cada breve reconstruye el muro de
   texto que este rediseño elimina y borra la jerarquía que lo justifica.

## Orden de trabajo sugerido

1. Instrumentar la cobertura de resúmenes en el build (independiente y sin riesgo).
2. Observar dos o tres corridas del cron.
3. Implementar la composición 1+4 con selección por puntaje, tope de seguridad y
   asunto derivado de la destacada.
4. Verificar en dry-run y con destinatario de prueba.
5. Activar la política de no envío con el umbral que respalde la medición.

La implementación fue autorizada explícitamente por el usuario después de la revisión
de Claude. Los pasos de observación en CI y envío a destinatario de prueba siguen siendo
operativos: requieren corridas reales y no forman parte de la verificación local.
