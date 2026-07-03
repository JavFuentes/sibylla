---
# Plantilla de publicación de Sibylla (sección SIBYLLA de la portada).
#
# Cómo publicar:
#   1. Copia este archivo con un nombre nuevo SIN el guion bajo inicial,
#      p. ej. `2026-07-15-mi-noticia.md` (los archivos `_*.md` se ignoran).
#   2. Rellena el front-matter y el cuerpo.
#   3. Commit + push: la publicación aparece en el siguiente build del cron.
#
# Campos:
#   titulo     (obligatorio) Título de la tarjeta.
#              ⚠️ Si la publicación NO lleva `url`, el título es su identidad
#              estable (ancla pública y cachés): no cambiarlo tras desplegar.
#   fecha      (obligatorio) YYYY-MM-DD, admite hora ("2026-07-15 12:00", UTC).
#              Una fecha futura pospone la publicación hasta el primer build
#              posterior (publicación programada).
#   resumen    (opcional) Bajada de 1-2 frases visible en la tarjeta; sin ella
#              se usa un recorte del cuerpo.
#   imagen     (opcional) Archivo publicado en static/ (p. ej. mi-foto.png) o
#              URL absoluta. Sin imagen se usa el placeholder de Sibylla.
#   url        (opcional) Enlace externo del título y del botón "Original".
#              SIN url, el build genera automáticamente una página propia para
#              la noticia en pub/<nombre-del-archivo>.html y la tarjeta enlaza
#              ahí; el cuerpo de este archivo es el contenido de esa página.
#              Con url externa, gana esa (no se genera página propia).
#   publicado  (opcional) `false` = borrador; no se publica hasta quitarlo o
#              ponerlo en `true`.
#
titulo: "Título de la noticia"
fecha: 2026-07-15
resumen: "Bajada de una o dos frases que se muestra en la tarjeta."
# imagen: mi-imagen.png
# url: https://ejemplo.cl/referencia
# publicado: false
---
Cuerpo opcional de la publicación, en texto plano (los saltos de párrafo se
respetan en pantalla). Se muestra al pulsar el botón "Resumen" de la tarjeta;
si no hay cuerpo, la tarjeta no muestra ese botón.
