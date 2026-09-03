# PLAN-BOLETIN-FRESCURA-IDIOMA.md — La destacada debe ser del día y estar en español

> **Estado:** propuesta. Diagnóstico verificado contra el código y contra los logs
> del run `33539752377` (cron del 1 de septiembre de 2026), que es la corrida que
> produjo el correo objetado. Ningún cambio aplicado todavía.

## 1. Qué salió mal

La primera edición con el formato editorial 1+4 (`c35ba74`) llegó con dos defectos
independientes:

1. **La señal del día era del 24 de agosto**, ocho días antes de la edición. La
   promesa del formato es «lo que está sucediendo»; una noticia de hace más de una
   semana la incumple.
2. **Llegó una tarjeta con título en inglés** (`Assessing digital health curriculum
   needs…`, PubMed) entre las señales breves.

Son dos causas distintas y se arreglan por separado.

## 2. Diagnóstico A — la selección de la destacada no mira la fecha

`newsletter._elegir_destacada()` ordena las candidatas por **sello** y luego por
**posición dentro de la sección**, y rompe los empates rotando la sección:

```python
mejor_tier = min(_tier(c) for c in candidatas)
candidatas = [c for c in candidatas if _tier(c) == mejor_tier]
mejor_posicion = min(int(c["position"]) for c in candidatas)
candidatas = [c for c in candidatas if int(c["position"]) == mejor_posicion]
```

No hay ninguna comparación de fecha en todo el módulo. La antigüedad de la tarjeta
no participa en la decisión.

### Por qué ganó Betelgeuse

La tarjeta de ALMA reunía las tres condiciones que premia ese orden: sello **TI**
(`alma` es tier 1), **posición 0** de su sección y una sección presente en los temas
del suscriptor. Compitió contra noticias del día de Actualidad en Chile y Frontera
Digital, ambas **TII**, y las venció por sello.

El agravante estructural está en `web.py`: la sección Astronomía admite fuentes
chilenas de hasta **30 días** (`ASTRO_PRIORITY_FRESH_DAYS = 30`) y agencias de hasta
**7** (`ASTRO_AGENCY_FRESH_DAYS = 7`), porque observatorios e instituciones publican
con poca frecuencia. Esa ventana es razonable para una tarjeta del sitio, pero
convierte a Astronomía en la sección que **casi siempre** aporta una tier I en
posición 0. Sin filtro de fecha, la destacada tenderá a ser una nota de observatorio
de hasta un mes de antigüedad, día tras día. Betelgeuse no fue un accidente: es el
resultado esperado del orden actual.

Nota adicional: `data/newsletter_edicion.json` ni siquiera transporta una fecha
utilizable. `CAMPOS_TARJETA` incluye `"date"`, pero `web._card()` lo llena con
`_fecha(it.published, …)`, que devuelve el texto de presentación `"24 ago 2026"`.
Hoy es imposible filtrar por fecha en el boletín aunque se quisiera.

## 3. Diagnóstico B — el inglés viene de un lote de traducción truncado

Evidencia literal del run `33539752377`:

```
17:49:24  Traduciendo 8 tarjetas a es con openai_compatible (deepseek-v4-flash)…
17:49:36  Traduciendo 8 tarjetas a es con openai_compatible (deepseek-v4-flash)…
17:50:21  Lote de traducción a es truncado (output=6000 tokens, 8 tarjetas);
          8 quedan en idioma original.
```

Un lote completo de ocho tarjetas superó el tope de salida de 6.000 tokens, el JSON
llegó cortado y `translate_cards()` decidió no reintentarlo:

```python
if hit_cap:
    log.warning("Lote de traducción a %s truncado …")
    break
```

La decisión de no reintentar es correcta **tal como está escrita** —repetir el mismo
lote lo truncaría igual— pero es una rendición: esas ocho tarjetas se publican en
inglés en el sitio y viajan en inglés al boletín. El lote afectado contenía material
de PubMed, cuyos *abstracts* son largos; ocho títulos + ocho snippets de esa longitud
agotan el presupuesto de salida.

Que sea un lote entero explica por qué el defecto no se vio en pruebas locales: no es
un fallo por tarjeta, es un fallo por lote, y solo aparece cuando el material del día
es voluminoso.

### Por qué el boletín no lo detectó

`web._card()` resuelve el idioma así:

```python
tr = (translations or {}).get(it.dedup_key)
title = tr["title"] if tr else it.title
```

Si no hubo traducción, cae al original **en silencio**. La tarjeta que llega a
`edicion_desde_contexto()` no lleva ninguna marca de idioma, así que el boletín no
tiene forma de distinguir un título en español de uno en inglés. El filtro que pide
el autor no puede escribirse sin añadir antes esa marca.

## 4. Cambios propuestos

### 4.1 Transportar la fecha real de publicación (`web.py`, `newsletter.py`)

- En `web._card()`, añadir `"published": it.published.isoformat() if it.published else ""`.
- Añadir `"published"` a `CAMPOS_TARJETA`.
- Subir `EDICION_SCHEMA` a `cl.sibylla.newsletter_edicion.v3`, sin período de
  compatibilidad. Vale el mismo argumento que en el plan editorial: el workflow
  construye y envía en la misma corrida, y `cargar_edicion()` rechaza cualquier otro
  esquema, así que no existe ventana en la que convivan dos versiones.

`"date"` se conserva: es el texto que ya usan las plantillas.

### 4.2 Bandas de frescura para la destacada (`newsletter.py`)

La antigüedad se mide en **días de calendario de Santiago**, no en horas, para que
coincida con lo que el lector entiende por «hoy» y con `edicion["fecha"]`:

```python
def _dia_santiago(iso: str) -> date | None   # published UTC → fecha local
```

`_elegir_destacada()` pasa a filtrar **antes** que cualquier otro criterio:

1. **Banda 1** — publicadas el mismo día de la edición.
2. **Banda 2** — publicadas el día anterior. Solo se usa si la banda 1 quedó vacía.
3. Si ambas quedan vacías, no hay destacada.

Dentro de la banda elegida se mantiene el orden actual sin cambios: sello, posición y
rotación determinista por `(fecha, uid)`. **La banda manda sobre el sello**: una
noticia de hoy con sello III gana a una de hace ocho días con sello I. Eso es
exactamente lo que pide el autor.

Las tarjetas sin `published` (fecha desconocida, «s/f») quedan fuera de las
candidatas a destacada: no se puede afirmar que sean del día.

### 4.3 Tope de antigüedad para las señales breves (`newsletter.py`)

Las breves no necesitan ser del día —su función es descubrir— pero tampoco pueden ser
de hace un mes. En `_cards_breves()`:

- Tarjetas noticiosas: **máximo 7 días**.
- Vídeos de Divulgación y publicaciones propias de la sección SIBYLLA: **exentos**.
  Son atemporales por naturaleza y su sección ya aplica su propia ventana
  (`DIVULGACION_FRESH_DAYS = 365`). Filtrarlos a 7 días dejaría sin correo al
  suscriptor que solo eligió Divulgación.

Si tras el filtro hay menos de cuatro breves, el correo sale con las que haya. La
destacada es la que sostiene la edición.

### 4.4 Marca de idioma en la tarjeta (`web.py`)

En `web._card()`, añadir un único campo booleano:

```python
card["es_espanol"] = _es_espanol(it, tr)
```

con la regla:

- **True** si la fuente declara `lang` español en `config/sources.yaml`
  (`es`, `es-419`) — 53 de las 97 fuentes, incluidos los 37 canales `yt_*`;
- **True** si se aplicó traducción (`tr` no es `None`);
- **True** para la sección SIBYLLA (`source_id == "sibylla"`), escrita en español y
  ausente del registro de fuentes;
- **False** en cualquier otro caso, incluidas las fuentes `multi`, `en`, `fr`, `it`
  que no recibieron traducción, y el APOD (`source_id == "apod"`, tampoco está en el
  registro) cuando falla la inyección de su título en español.

La combinación de las dos señales es deliberada. Usar solo «¿tiene traducción?»
haría que, sin proveedor LLM, **todas** las tarjetas quedaran marcadas como no
españolas y el boletín se quedara sin material. Con la primera regla, una corrida sin
LLM sigue teniendo las 53 fuentes en español disponibles.

El campo se calcula con `config.load_registry()` + `index_by_id()`, resueltos una vez
por build y no por tarjeta.

### 4.5 Filtro de idioma en el boletín (`newsletter.py`)

- `_candidatas()` descarta toda tarjeta con `es_espanol` falso: nunca será destacada.
- `_cards_breves()` aplica el mismo descarte: nunca será una señal breve.

Con esto, aunque la traducción vuelva a fallar, **el correo no puede llevar inglés**.
El sitio sí lo llevará hasta la siguiente corrida; eso lo ataca 4.6.

Compatibilidad: si el campo no existe (edición de un build anterior), se trata como
**verdadero**, para que un despliegue a medias no vacíe el boletín.

### 4.6 Causa raíz: bisecar el lote truncado (`translate.py`)

En lugar de rendirse cuando `hit_cap` es verdadero, partir el lote en dos mitades y
traducir cada una por separado, recursivamente, hasta un lote de tamaño 1:

- Un lote de 8 que trunca cuesta 2 llamadas más (dos de 4); si una mitad vuelve a
  truncar, 2 más (dos de 2). El coste solo se paga cuando hay truncamiento.
- Con lote de tamaño 1 que aún trunca, esa tarjeta sí cae al idioma original: es un
  caso real de snippet desmesurado y no hay nada que partir. Debe registrarse con su
  `id` para poder inspeccionarlo.
- No tocar `_CHUNK_SIZE = 8` ni `max_tokens = 6000`: subir el tope trasladaría el
  problema a lotes más grandes en vez de resolverlo.

Este cambio beneficia al sitio tanto como al boletín, y es independiente de todo lo
anterior: puede implementarse y desplegarse solo.

### 4.7 Observabilidad

Junto a la línea que ya existe (`boletín: resúmenes elegibles 22/24`), registrar en
el build:

```
boletín: candidatas hoy=N ayer=M sin_fecha=K descartadas_idioma=J
```

Sirve para tres cosas: ver si la banda 1 se queda vacía con frecuencia (el cron corre
a las 14:08 UTC, ~10:08 en Santiago, así que «hoy» cubre pocas horas de publicación),
medir cuánto material pierde el filtro de idioma, y alimentar con datos reales el
umbral de la política «sin resumen, no se envía» que quedó pendiente en
[PLAN-BOLETIN-EDITORIAL.md](PLAN-BOLETIN-EDITORIAL.md).

## 5. Qué pasa si no hay destacada fresca

`componer_boletin()` devuelve `None`, que es el camino que el código ya recorre
cuando no hay ningún resumen elegible. El comportamiento actual encaja sin cambios:

- el suscriptor entra en `estado["omitidos"]` y se registra el motivo;
- `pendientes()` solo excluye a los de `enviados`, así que el día **no** queda
  `terminado`;
- el cron de respaldo de las 14:38 UTC lo reintenta con la edición regenerada.

Si a esa hora tampoco hay nada del día ni del anterior, ese día no sale correo. Es la
consecuencia deliberada de la regla que pide el autor, y es preferible a enviar una
edición que incumple su propia promesa. Debe emitirse un `::warning::` visible en el
run para que la ausencia de correo nunca sea silenciosa.

## 6. Riesgos

1. **Quedarse sin destacada más días de lo previsto.** El cron corre a media mañana
   de Santiago; la banda «hoy» es estrecha. Mitigación: la banda 2 (ayer) cubre el
   ciclo completo de publicación del día anterior, y 4.7 mide si aun así falla. Si la
   medición muestra huecos, la conversación siguiente es adelantar o añadir un cron,
   no ampliar la ventana.
2. **Astronomía casi desaparece de la destacada.** Es la consecuencia correcta: sus
   ventanas de 30 y 7 días existen para llenar una sección del sitio, no para elegir
   la noticia del día. Seguirá apareciendo en las breves.
3. **El filtro de idioma reduce el material elegible.** En la corrida analizada
   habría descartado 8 tarjetas de 24. Con 4.6 aplicado, ese número debería tender a
   cero; conviene desplegar 4.6 **antes** que 4.5 para no estrechar la selección
   mientras la causa raíz sigue viva.
4. **Fuentes mal declaradas en `sources.yaml`.** Una fuente en español declarada
   `lang: en` sin traducción quedaría descartada. Las 97 fuentes tienen `lang`
   declarado (0 vacíos), pero conviene revisar las 6 marcadas `multi`.
5. **Bisección y coste LLM.** Solo se activa ante truncamiento y está acotada por la
   profundidad del árbol; el gasto adicional es marginal frente a una edición en
   inglés.

## 7. Verificación

1. Tests nuevos en `tests/test_newsletter.py`:
   - destacada de hoy con sello III gana a una de hace ocho días con sello I;
   - sin candidatas de hoy, gana la de ayer;
   - sin candidatas de hoy ni de ayer, `componer_boletin()` devuelve `None`;
   - tarjeta sin `published` nunca es destacada;
   - tarjeta con `es_espanol` falso no aparece ni como destacada ni como breve;
   - edición sin el campo `es_espanol` se comporta como antes (compatibilidad);
   - breve noticiosa de 10 días descartada; vídeo de 30 días conservado.
2. Tests en `tests/test_translate.py`: un lote que trunca se bisecta y recupera las
   tarjetas; un lote de tamaño 1 que trunca registra el `id` y cae al original.
3. Test en `tests/test_web.py`: `_card()` emite `published` en ISO y `es_espanol`
   correcto para fuente `es`, fuente `en` traducida, fuente `en` sin traducir,
   sección SIBYLLA y APOD.
4. Suite completa (`pytest`), que hoy está en 553 tests.
5. `SIBYLLA_NEWSLETTER_DRYRUN=1` en una corrida real; comprobar la línea de
   observabilidad de 4.7.
6. Envío a `SIBYLLA_NEWSLETTER_TEST_TO` y revisión del correo: fecha de la destacada,
   ausencia de inglés, cuatro breves o menos.
7. Solo entonces, cron normal.

## 8. Criterios de aceptación

- La destacada es del día de la edición o, como máximo, del día anterior.
- Ninguna tarjeta del correo —destacada o breve— está en un idioma distinto del
  español.
- Ninguna señal breve noticiosa supera los 7 días; los vídeos y las publicaciones
  propias quedan exentos.
- Si no hay destacada fresca, no se envía correo degradado y el run deja un
  `::warning::` explícito.
- Un lote de traducción truncado ya no condena a sus 8 tarjetas al idioma original.
- El build registra candidatas por banda y descartes por idioma.
- La personalización, la baja, la privacidad de los logs, el envío individual, la
  reanudación y la idempotencia siguen intactas.
- El sitio y sus resúmenes no cambian como efecto lateral, salvo por las traducciones
  que 4.6 recupera.

## 9. Orden de trabajo

1. **4.6 (bisección de traducción)**, solo y desplegado primero: ataca la causa raíz,
   no toca el boletín y mejora el sitio de inmediato.
2. **4.1 + 4.4** (transporte de `published` y marca de idioma, esquema v3).
3. **4.2 + 4.3 + 4.5** (bandas de frescura, tope de las breves y filtro de idioma).
4. **4.7** (observabilidad) junto con el punto 3.
5. Dry-run, destinatario de prueba y cron normal, en ese orden.

## 10. Hallazgo lateral, fuera de alcance

`web.py:679` emite el JSON-LD con `"datePublished":"24 ago 2026"`, que no es una
fecha válida para schema.org: debería ser ISO 8601. El campo `published` que añade
4.1 lo dejaría a un cambio de una línea. No forma parte de este plan; queda anotado
para decidirlo aparte.
