# Informe de traspaso: fallo del boletín diario

Fecha del informe: 25 de julio de 2026  
Repositorio: `JavFuentes/sibylla`  
Objetivo: entregar a Fable evidencia suficiente para auditar, reproducir y corregir la implementación del boletín.

## 1. Resumen ejecutivo

La implementación no consiguió demostrar una entrega real de extremo a extremo.
El sitio permitió crear dos suscripciones y los workflows aparecieron verdes,
pero no llegó ningún correo.

Hay al menos un defecto lógico confirmado:

1. La primera ejecución de producción ocurrió inmediatamente después de
   desplegar por primera vez la interfaz de suscripción. En ese momento no podía
   haber suscriptores creados desde la web.
2. Una lista vacía se trató como ejecución exitosa y se guardó
   `newsletter_state.json` con el día marcado como `terminado`.
3. Las dos cuentas se inscribieron después, durante el mismo día.
4. El cron natural descargó ese estado y salió **antes de volver a consultar
   Firestore**, por lo que nunca vio las dos altas nuevas.

La evidencia exacta del cron es:

- Run principal programado: `30163152682`.
- Inicio: `2026-07-25T15:12:20Z`.
- Descargó `newsletter_state.json` de 208 bytes.
- Tenía `SIBYLLA_NEWSLETTER_DRYRUN=0` y
  `SIBYLLA_NEWSLETTER_TEST_TO` vacío.
- El comando terminó con: `boletín: la edición de hoy ya terminó`.
- No hubo lectura de Firestore ni intento SMTP para los dos suscriptores.

El correo de prueba constituye un segundo problema, todavía no resuelto:
GitHub inyectó el destinatario de prueba, el puerto SMTP estaba accesible y no
apareció una excepción SMTP, pero el mensaje no llegó. La versión ejecutada no
registraba explícitamente si `send_message()` había sido alcanzado y aceptado,
por lo que ese run no permite distinguir entre entrega aceptada, omisión o una
limitación de observabilidad.

## 2. Cambios implementados

Commit principal:

- `6b0a0a9 feat: añade boletín diario personalizado`

Correcciones posteriores:

- `cbdd351 fix: protege el destinatario de prueba`
- `f2e388e fix: informa el resultado del correo de prueba`

El commit principal modificó 25 archivos, con aproximadamente 1.452 líneas
añadidas. Los componentes principales son:

### Backend del boletín

- `sibylla/newsletter.py`
  - Genera `data/newsletter_edicion.json` usando el contexto de la portada.
  - Personaliza hasta 12 tarjetas por destinatario mediante round-robin.
  - Renderiza HTML y texto plano.
  - Construye cabeceras `List-Id`, `List-Unsubscribe`, `Precedence` y
    `Auto-Submitted`.
  - Envía un mensaje SMTP independiente por destinatario.
  - Guarda `data/newsletter_state.json` después de cada destinatario.
  - Implementa `dry-run` y destinatario de prueba.
  - Captura excepciones y devuelve siempre control al workflow.

- `sibylla/suscriptores.py`
  - Obtiene un token OAuth desde `SIBYLLA_FIREBASE_SA_JSON`.
  - Lista `suscripciones` mediante la API REST de Firestore.
  - Solo acepta documentos con `v == 1`, `activa == true`, UID, correo y al
    menos un tema válido.
  - Ante cualquier error devuelve `[]` para no romper el despliegue.

- `sibylla/templates/newsletter.html.j2`
- `sibylla/templates/newsletter.txt.j2`

### Interfaz y Firestore

- `static/social.js`
  - Modal de suscripción integrado con Firebase Auth.
  - Exige usuario con correo verificado.
  - Escribe un documento `suscripciones/{uid}` con los campos:
    `v`, `uid`, `email`, `activa`, `temas`, `creada`, `actualizada`.
  - Permite activar, desactivar o borrar la suscripción.

- `sibylla/templates/index.html.j2`
  - Añade entrada de menú, modal y textos del boletín.

- `firestore.rules`
  - Solo el propietario autenticado puede leer/escribir/borrar su documento.
  - Para crear o actualizar exige `email_verified == true` y esquema cerrado.
  - La cuenta de servicio del build lista la colección fuera de estas reglas.

### CLI y automatización

- `sibylla/cli.py`
  - `--newsletter` genera la edición.
  - `--newsletter-send` intenta enviarla.

- `.github/workflows/regenerate.yml`
  - Genera la edición junto con la web.
  - Publica la web antes de enviar.
  - Hace un preflight TCP al host/puerto SMTP.
  - Ejecuta el envío con `continue-on-error: true`.
  - Descarga y persiste `newsletter_state.json` en el host por SCP.
  - Cron: `14:08 UTC` y respaldo `14:38 UTC`; GitHub puede retrasarlos.

## 3. Configuración operativa realizada

No se incluyen valores secretos en este informe.

- Buzón: `noticias@sibylla.cl` en Hostinger.
- SMTP: `smtp.hostinger.com`, puerto 465, modo SSL.
- Límite informado por Hostinger: 100 mensajes salientes cada 24 horas.
- Tope configurado por el boletín: 80.
- SPF presente con el include de Hostinger.
- DKIM aparecía verificado en hPanel.
- Se detectaron dos registros DMARC simultáneos. Se indicó conservar solo uno,
  pero este informe no confirma mediante DNS que la duplicación haya sido
  eliminada.
- Hostinger rechazó `noticias+baja@sibylla.cl`; se reemplazó por el alias real
  `baja@sibylla.cl`.
- Se creó la carpeta `Bajas` y un filtro funcional para ese alias.
- Cuenta de servicio observada:
  `conteos-reader@sibylla-a81d2.iam.gserviceaccount.com`, con rol
  `Lector de Cloud Datastore`.
- Las reglas de Firestore compilaron en dry-run y posteriormente se desplegaron.
- Se configuraron en GitHub los secretos SMTP y de Firebase, y variables de
  modo, límite, throttle y URL.

Nota de seguridad: durante la configuración se expuso una contraseña SMTP en la
conversación. Se recomendó rotarla inmediatamente. Fable debe confirmar que el
secret actual contiene una credencial nueva y no la expuesta.

## 4. Cronología y evidencia de ejecución

### 4.1 Primera ejecución que contaminó el estado diario

Run: `30147449710`  
Commit: `6b0a0a9`  
Evento: manual (`workflow_dispatch`)  
Inicio: `2026-07-25T06:24:48Z`

Datos del log:

- No existía `newsletter_state.json` remoto.
- La interfaz de suscripción se publicó en ese mismo run.
- `SIBYLLA_NEWSLETTER_DRYRUN=0`.
- `SIBYLLA_NEWSLETTER_TEST_TO` estaba vacío por una configuración inicial
  incorrecta entre Secrets y Variables.
- El paso SMTP encontró el host/puerto accesible.
- Se ejecutó `python -m sibylla.cli --newsletter-send`.
- Inmediatamente después se creó y persistió `newsletter_state.json`.

Conclusión: el primer run entró involuntariamente en modo producción cuando
todavía no podía haber suscripciones creadas desde la web. El código consideró
la lista vacía como finalización exitosa.

### 4.2 Prueba dirigida que no llegó

Run: `30148219239`  
Commit: `cbdd351`  
Evento: manual  
Inicio: `2026-07-25T06:50:41Z`

Datos del log:

- `SIBYLLA_NEWSLETTER_DRYRUN=0`.
- `SIBYLLA_NEWSLETTER_TEST_TO=***`; GitHub sí inyectó un secreto no vacío.
- Host/puerto SMTP accesible.
- No se registró excepción de autenticación, destinatario, remitente o datos.
- El comando acabó sin salida de confirmación.
- El usuario no recibió el mensaje en bandeja principal, promociones ni spam.

Limitación: esa versión solo registraba errores. No registraba un acuse tras
`SMTP.send_message()`, ni la respuesta del servidor, ni un identificador de
cola. Por ello no existe prueba concluyente de aceptación SMTP.

### 4.3 Cron natural que no envió a las dos altas

Run: `30163152682`  
Commit: `f2e388e`  
Evento: `schedule`  
Inicio: `2026-07-25T15:12:20Z`

Datos del log:

- Descargó un estado remoto de 208 bytes.
- Generó y desplegó la web correctamente.
- `SIBYLLA_NEWSLETTER_DRYRUN=0`.
- `SIBYLLA_NEWSLETTER_TEST_TO` vacío: debía usar Firestore.
- Salida exacta: `boletín: la edición de hoy ya terminó`.
- Duración del comando: aproximadamente 0,2 segundos.

El flujo en `enviar_boletin_cli()` comprueba `debe_enviar(estado, hoy)` antes de
llamar a `fetch_suscriptores()`. Por tanto, este run no consultó Firestore y no
intentó enviar a las dos cuentas.

El segundo cron (`30164203008`) fue el respaldo y se saltó correctamente porque
el primer cron figuraba como exitoso.

## 5. Defectos confirmados

### A. Estado diario cerrado con cero suscriptores

`enviar()` hace:

```python
estado["terminado"] = not fatal and not truncado
```

Con una lista vacía, `fatal == False` y `truncado == False`, de modo que marca
el día como terminado. Esto es incorrecto durante el lanzamiento y ante altas
posteriores del mismo día.

### B. Guardia de estado antes de leer Firestore

`enviar_boletin_cli()` carga el estado y retorna si el día figura terminado;
solo después de esa guardia llama a `fetch_suscriptores()`. Nuevos suscriptores
del mismo día nunca se comparan con los UID ya enviados.

### C. Error de Firestore indistinguible de cero suscriptores

`fetch_suscriptores()` devuelve `[]` ante cualquier error de autenticación,
permisos, red o formato. La capa superior interpreta ese `[]` como una lista
válida y puede marcar la edición como terminada. El resultado debería distinguir
`lectura correcta con cero documentos` de `lectura fallida`.

### D. Workflow verde aunque el correo falle

- El paso de envío tiene `continue-on-error: true`.
- `enviar_boletin_cli()` captura cualquier excepción y devuelve siempre `0`.
- El preflight comprueba solo apertura TCP, no TLS, autenticación ni aceptación
  de un mensaje.

Por diseño, el estado verde de GitHub no acredita que el boletín haya sido
enviado.

### E. Prueba SMTP no era una prueba de extremo a extremo observable

El modo `SIBYLLA_NEWSLETTER_TEST_TO` evita Firestore y usa un suscriptor
sintético. Sirve para probar render y SMTP, pero no prueba la lectura de
suscripciones reales. Además, la primera versión no imprimía un resultado
explícito tras `send_message()`.

## 6. Aspectos no resueltos que Fable debe verificar

1. **Entrega SMTP real:** ejecutar una prueba controlada y capturar la respuesta
   de `MAIL FROM`, `RCPT TO` y `DATA`, sin activar trazas que filtren secretos o
   direcciones completas.
2. **Logs de Hostinger:** revisar si el mensaje de prueba entró en la cola,
   rebotó, fue diferido o nunca fue recibido por el servidor SMTP.
3. **Valor exacto de `SMTP_FROM`:** comprobar que sea una dirección autorizada
   por el buzón autenticado.
4. **Contraseña rotada:** confirmar que el secret no conserva la credencial
   expuesta.
5. **DMARC:** consultar DNS autoritativo y asegurar que existe un solo TXT en
   `_dmarc.sibylla.cl`.
6. **Firestore vivo:** listar de forma read-only los documentos y verificar que
   los dos tienen `v=1`, `activa=true`, UID correcto, correo y temas válidos.
7. **Cuenta de servicio:** probar explícitamente el endpoint REST con la misma
   identidad usada en Actions.
8. **Estado remoto:** inspeccionar el JSON de 208 bytes sin exponer correos; debe
   aclararse `total`, `enviados`, `omitidos`, `fallidos` y `terminado`.
9. **Zona y versión de edición:** el estado solo se identifica por fecha de
   Santiago. No contiene hash de edición ni snapshot/versionado del conjunto de
   suscriptores.

## 7. Pruebas realizadas y huecos de cobertura

Antes del commit principal se ejecutó la suite completa: 553 tests pasaron y uno
fue omitido. También se validaron sintaxis JavaScript y reglas. Sin embargo:

- Los tests SMTP usan objetos falsos y monkeypatch de `_abrir_smtp`.
- No hubo prueba de integración contra Hostinger.
- No hubo prueba end-to-end GitHub Actions → Firestore real → Hostinger →
  bandeja receptora.
- No hubo caso que simulara: corrida con cero suscriptores, alta posterior y
  segunda corrida el mismo día.
- No hubo caso que exigiera que un fallo de Firestore dejara el estado
  reanudable.
- El commit `f2e388e` solo pudo validarse localmente con `py_compile` porque el
  entorno virtual local estaba roto; no se ejecutó pytest local para ese commit.

## 8. Dirección de corrección sugerida para auditoría

Estas son hipótesis de trabajo, no cambios aplicados:

1. Consultar Firestore antes de decidir que el día terminó.
2. Calcular pendientes como `suscriptores_actuales - enviados - omitidos`, aun
   cuando `terminado` sea verdadero.
3. No cerrar el estado con cero suscriptores durante una corrida de lanzamiento,
   o guardar un snapshot/hash que permita detectar altas posteriores.
4. Hacer que `fetch_suscriptores()` devuelva un resultado tipado con estado
   `ok/error`, no una lista vacía ambigua.
5. Separar el resultado del despliegue del resultado del boletín y emitir una
   anotación/alerta visible si no hubo intento o si falló.
6. Persistir contadores agregados seguros: leídos, válidos, pendientes,
   aceptados, omitidos y fallidos, sin direcciones completas.
7. Añadir una prueba de humo autenticada que confirme login SMTP y aceptación
   del mensaje antes de habilitar producción.
8. Añadir tests de regresión para nuevas altas el mismo día, Firestore caído,
   lista vacía y reanudación parcial.

## 9. Archivos prioritarios para revisar

1. `sibylla/newsletter.py`: `debe_enviar`, `enviar`, `enviar_boletin_cli`.
2. `sibylla/suscriptores.py`: `fetch_suscriptores` y su contrato de error.
3. `.github/workflows/regenerate.yml`: pasos de descarga, envío y persistencia.
4. `static/social.js`: `guardarSuscripcion` y esquema de documentos.
5. `firestore.rules`: bloque `suscripciones/{uid}`.
6. `tests/test_newsletter.py` y `tests/test_suscriptores.py`.

## 10. Estado actual del repositorio

- Rama: `main`.
- HEAD al redactar el informe: `f2e388e`.
- El informe se crea como archivo local y **no se commitea ni se sube** sin una
  nueva instrucción explícita del usuario.

