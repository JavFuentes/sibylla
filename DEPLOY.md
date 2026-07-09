# DEPLOY.md — Publicar y automatizar la web de Sibylla

Guía **genérica** (independiente del proveedor) para subir el sitio a un hosting
estático y mantenerlo al día. Para arquitectura y convenciones, ver
[AGENTS.md](AGENTS.md); para uso del CLI, [README.md](README.md).

---

## 1. Qué se publica

La web de Sibylla es **100 % estática** (HTML + CSS, sin servidor de aplicación
ni base de datos). El generador produce, en la carpeta `web/`:

```
web/
  index.html          ← la portada del sitio (español, una sola página)
  pub/<slug>.html     ← una página por publicación propia SIBYLLA sin `url`
                         externa (se autogenera en cada build)
```
(El `dashboard.html` de métricas **no** se genera aquí ni se publica — es una
herramienta de monitoreo local, ver §1.1. La subida es `scp -r web/` (o
equivalente), así que `pub/` se publica sin pasos extra.)

> **`web/` está en `.gitignore`**: es un *artefacto generado*, no se versiona.
> Desplegar = **regenerar** `web/` y **subir su contenido** a la raíz pública del
> hosting. No hay nada que "compilar" en el servidor.

### 1.1 Herramienta admin local (personal, no se publica)

La herramienta admin local incluye el dashboard de métricas y páginas de
administración del sitio. Hoy tiene dos secciones:
- `/metricas`: historial de regeneraciones, consumo de tokens por llamada LLM
  (summarize + traducciones por idioma) y costo estimado en USD
- `/divulgacion`: ver, añadir y quitar canales de YouTube de la sección Divulgación

Es una **herramienta personal del operador**: no le interesa al visitante, así
que **no se publica** en el sitio. El build del sitio (`--html`) **no** genera
`dashboard.html`, y el deploy nunca lo sube. `--dashboard` sirve páginas desde
un servidor local en `127.0.0.1` y no escribe `web/dashboard.html`.

**Para abrirla en local** (descarga el historial de producción del host en modo
best-effort, arranca el servidor y abre tu navegador):

```bash
python -m sibylla.cli --dashboard
```

Necesita en tu `.env` las credenciales SSH del host (`DEPLOY_HOST`,
`DEPLOY_USER`, `DEPLOY_PORT`, `DEPLOY_DATA_PATH` y, opcional, `DEPLOY_KEY_FILE`)
— las mismas del deploy — para poblar `/metricas` con el historial de producción.
Si la descarga falla, `/metricas` muestra el historial local o vacío, y
`/divulgacion` sigue funcionando.

La gestión de canales de `/divulgacion` edita `config/sources.yaml` y
`sibylla/pipeline.py` por cirugía de texto, muestra un banner de cambios
pendientes de commit y **no commitea ni pushea**. Para resolver `@handle` usa
`YOUTUBE_API_KEY` si está definida; sin clave, cae a scraping del HTML del canal.

Los datos de cada ejecución se persisten en `data/runs.json` (ignorado por git).
En CI el historial vive en el **host**, no en el cache de Actions: cada corrida
descarga el `runs.json` del servidor, le añade la corrida nueva y lo vuelve a
subir (read-modify-write protegido por el `concurrency` del workflow). Se guarda
en una ruta privada fuera de la raíz pública (`DEPLOY_DATA_PATH`, por defecto
`~/.sibylla/runs.json`), así que no se sirve por web. El cache de Actions solo
guarda las traducciones (regenerables); el historial ya no depende de él, que se
evicta a los 7 días. En local, el historial crece con cada `--html`.

### El LLM es de *build-time*, no del visitante

Las tarjetas (título + snippet) se traducen a los 4 idiomas **al generar** y
quedan horneadas en el HTML. El visitante solo descarga HTML ya traducido: **no
hace falta ninguna clave de IA en el navegador ni en el servidor**. La API key
del LLM es un secreto del *operador*, usado solo al construir (ver §4).

---

## 2. Generar el sitio

```bash
# Con LLM configurado en .env -> tarjetas traducidas por idioma (recomendado)
python -m sibylla.cli --topics ai,medicine --html --translate auto

# Sin traducir contenido (tarjetas en idioma original de la fuente)
python -m sibylla.cli --topics ai,medicine --html --translate off
```

- `--translate auto` traduce si hay LLM en `.env`; si no, cae al idioma original
  sin romper la corrida.
- Las traducciones se cachean en `data/translations.json` (ignorado por git):
  regenerar solo vuelve a traducir los ítems **nuevos**, así que es barato.

Comprobación rápida antes de subir: abre `web/pt.html` y `web/en.html` en el
navegador y verifica que los titulares aparecen en portugués / inglés.

---

## 3. Subir `web/` al hosting (cualquier proveedor)

Copia **el contenido** de `web/` a la **raíz pública** de tu hosting (el
directorio que el proveedor sirve como sitio: suele llamarse `public_html`,
`www`, `htdocs` o similar). Elige el método que ofrezca tu proveedor:

| Método | Cómo |
| --- | --- |
| **Gestor de archivos del panel** | Sube/arrastra los archivos de `web/` a la raíz pública. Lo más simple para una primera vez. |
| **FTP / FTPS** | Cliente tipo FileZilla → conecta con host/usuario/clave del proveedor → sube `web/*` a la raíz pública. |
| **SFTP (SSH)** | `sftp` o `scp -r web/* usuario@host:/ruta/publica/` si el proveedor da acceso SSH. |
| **rsync (SSH)** | `rsync -az web/ usuario@host:/ruta/publica/` — solo sube lo que cambió; ideal para automatizar. |

> **No necesitas configurar nada de servidor**: es HTML estático. La mayoría de
> hostings sirven `index.html` como documento por defecto automáticamente.

### Comportamiento del aterrizaje (redirección por idioma)

`index.html` incluye un pequeño JS que:

1. Si el usuario **ya eligió** idioma antes, respeta su preferencia
   (`localStorage`, clave `sibylla_lang`) y redirige a `{idioma}.html`.
2. Si no, detecta el idioma del navegador (`navigator.language`) y, si está
   entre los soportados (`es`, `en`, `it`, `pt`), redirige a esa página.
3. Si no coincide ninguno, se queda en español (el contenido de `index.html`).

Como los enlaces son **relativos**, el sitio funciona igual en la raíz del
dominio o en un subdirectorio. Requisito único del host: servir `index.html`
como documento índice (comportamiento estándar).

---

## 4. Automatizar la regeneración periódica

El sitio envejece (las noticias cambian), así que conviene regenerarlo cada
cierto tiempo. Tres vías, de más a menos automática:

### A) GitHub Actions → subir al host por SSH *(recomendada; ya incluida)*

El workflow [`.github/workflows/regenerate.yml`](.github/workflows/regenerate.yml)
corre en `cron`, regenera el sitio en CI y lo sube a tu host por `scp`/SSH.
Tu clave de IA y las credenciales del host viven como **secrets cifrados de
GitHub** — nunca en el repo (que es público).

Configura en *Settings → Secrets and variables → Actions* del repo:

| Secret | Para qué |
| --- | --- |
| `LLM_PROVIDER`, `LLM_MODEL` | Proveedor y modelo de IA (pueden ir como *Variables* en vez de *Secrets*). |
| `LLM_API_KEY` | Clave del proveedor de IA. **Secret.** |
| `X_BEARER_TOKEN` | Token Bearer de X para "Voces de la red" (solo si usas `--with-x`). **Secret.** |
| `YOUTUBE_API_KEY` | Clave de la *YouTube Data API v3* para la sección Divulgación. Gratis (10.000 unidades/día; el build gasta ~40). Sin ella se cae al feed RSS, que YouTube throttlea desde las IPs de CI (404/500). **Secret.** |
| `BLUESKY_IDENTIFIER` | Identificador de Bluesky (ej. `sibylla.bsky.social`) para la API de AT Protocol. |
| `BLUESKY_APP_PASSWORD` | App password de Bluesky (desde Settings → App Passwords). **Secret.** |
| `MASTODON_INSTANCE` | Instancia de Mastodon (opcional; por defecto `mastodon.social`). |
| `DEPLOY_HOST`, `DEPLOY_USER` | Host y usuario SSH/SFTP del hosting. |
| `DEPLOY_KEY` | Clave **privada** SSH autorizada en el host. **Secret.** |
| `DEPLOY_PATH` | Ruta de la raíz pública en el host (p. ej. `/home/usuario/public_html`). |
| `DEPLOY_PORT` | Puerto SSH (opcional; por defecto `22`). |
| `DEPLOY_DATA_PATH` | *Variable* (no secret) con la ruta privada para `runs.json`, fuera del web root. Opcional; por defecto `.sibylla` (relativa al home SSH). |

Disparo manual: pestaña *Actions → Regenerar sitio Sibylla → Run workflow*.

#### APOD temprano (para Stellar-View)

NASA publica el APOD del día ~2-3 AM hora de Chile, varias horas antes de que
corra el build de arriba. Sin nada más, la traducción es/it del APOD de "hoy"
(`apod-i18n.json`, ver §"APOD i18n" en `sibylla/apod.py`) quedaba vieja durante
esa ventana y Stellar-View mostraba el texto en inglés de NASA hasta las 11.

El workflow [`.github/workflows/regenerate-apod.yml`](.github/workflows/regenerate-apod.yml)
corre `python -m sibylla.cli --apod-only` en un cron aparte y más temprano
(07:00 y 10:00 UTC), publicando `apod-i18n.json` — no toca noticias,
YouTube, X ni el historial de métricas. Reusa los mismos secrets de la tabla
de arriba (`NASA_API_KEY`, `LLM_*`, `DEPLOY_*`); no requiere configurar nada
adicional. Es idempotente: el build de las 11 lo vuelve a escribir sin
problema.

Cada corrida además deja una copia inmutable en `apod-i18n/<fecha>.json`
(dentro de `DEPLOY_PATH`), que nunca se sobrescribe: es el archivo histórico
que le permite a Stellar-View mostrar traducciones de APODs de días
anteriores (desde que este mecanismo empezó a correr; antes de eso, la app
cae al inglés de NASA). Se sube con `scp -r` sobre un directorio remoto ya
existente, que mergea en vez de purgar — así se acumula un archivo por día
sin necesitar el patrón descargar/subir que usa `runs.json`. La subida del
histórico es *best-effort*: si falla, no aborta el workflow (el APOD de
"hoy" ya quedó publicado; ese día solo no se archiva hasta la próxima
corrida exitosa).

### B) GitHub Actions → GitHub Pages

Si prefieres no gestionar credenciales de host: publica en **GitHub Pages** y
apunta `sibylla.cl` a Pages por DNS (registro `CNAME` / `A` según GitHub). Solo
necesitas `LLM_API_KEY` como secret. Requiere **repuntar el DNS** del dominio a
GitHub. (No incluido por defecto; se construye sobre el mismo paso de build
reemplazando el deploy por `actions/upload-pages-artifact` + `actions/deploy-pages`.)

### C) Cron local / VPS

Si tienes una máquina encendida (o un VPS), programa el build + subida con `cron`
(Linux/Mac) o el **Programador de tareas** (Windows). La clave vive solo en el
`.env` de ese entorno. Ejemplo de entrada `crontab` (diario a las 11:00):

```cron
0 11 * * *  cd /ruta/a/Sibylla && /ruta/al/.venv/bin/python -m sibylla.cli \
            --topics ai,medicine --html --translate auto && \
            rsync -az web/ usuario@host:/ruta/publica/
```

---

## 5. Seguridad (no negociable)

- **Nunca** subas `.env` ni pegues claves en commits, issues o chats. `.env` está
  en `.gitignore`; mantenlo así.
- En CI, las claves van **solo** como *encrypted secrets*, nunca hardcodeadas en
  el YAML.
- La automatización por defecto **no** usa `--with-x` (X es de pago por uso, con
  tope mensual en `config/sources.yaml`). Inclúyelo solo a conciencia.
- Mastodon es **gratis y sin auth** en instancias públicas (`mastodon.social` por defecto).
  Bluesky requiere credenciales gratuitas (app password) — sin ella
  simplemente se omite con `log.warning` y los fallbacks mantienen las 6 tarjetas.
- No subas `output/` ni `data/` al hosting: no son parte del sitio.

---

## 6. App Check (fase social) — Supervisión; Enforce listo tras configurar la SA

La web inicializa **App Check** con reCAPTCHA v3. La *Site key* pública viaja en
el bloque `social-i18n` del template (`appCheckSiteKey`, activada 2026-07-05); el
init está cableado en `static/social.js` (degrada en silencio si falla). Firestore
está en modo **Supervisión** (mide peticiones verificadas vs no verificadas, **no
bloquea**).

El build lee `agregados/conteos` por REST para hornear los conteos en el HTML.
**Sin Enforce** basta la API key pública (anónimo). **Con Enforce** esa lectura
anónima se bloquea, así que el build usa una **cuenta de servicio**:

- `sibylla/social_sync.py` detecta credenciales en `SIBYLLA_FIREBASE_SA_JSON`
  (JSON inline) o `GOOGLE_APPLICATION_CREDENTIALS` (ruta) y, si las hay, llama al
  mismo REST con `Authorization: Bearer <token OAuth>` (scope `datastore`) en vez
  de `?key=`. Sin credenciales o si falla, cae al camino anónimo (que tras Enforce
  devuelve `{}` → sitio sin números, sin romper el build).
- `google-auth` es la dependencia nueva; el resto del build sigue siendo
  `requests` puro.

### Pasos para activar Enforce (operador, manual)

1. **Crear la service account** en el proyecto Firebase `sibylla-a81d2` (¡no
   confundir con `sibylla-501104`, el de la key de YouTube!) con rol mínimo
   `roles/datastore.viewer`. Generar una **key JSON** (consola GCP → IAM →
   Service accounts; `gcloud` no está en la máquina del autor, usar la web).
2. **Cargar el secret** `SIBYLLA_FIREBASE_SA_JSON` en GitHub (contenido del JSON)
   y, si se prueba local, pegarlo en `.env` o apuntar `GOOGLE_APPLICATION_CREDENTIALS`.
3. **Verificar un build** con la SA: regenerar y comprobar que los conteos se
   hornean en el HTML (`#social-conteos`). En Supervisión, una lectura REST
   anónima con la API key pública debe seguir funcionando (todavía no bloquea).
4. **Activar Enforce** (consola Firebase → App Check → Firestore → "Aplicar")
   solo tras confirmar en Supervisión que ~100 % de las peticiones llegan
   verificadas **y** un build de producción con la SA hornea los conteos. Tras
   activarlo, votar/comentar/leer siguen funcionando y una lectura REST anónima
   con la API key pública **falla** (esperado). Rollback: volver a Supervisión
   (un clic).

- La CSP de `static/.htaccess` ya permite reCAPTCHA (`www.google.com`,
  `www.gstatic.com` en `script-src`/`connect-src`/`frame-src`): no requiere cambios.
- La *Secret key* de reCAPTCHA vive **solo** en la consola de App Check, nunca en
  el repo. La key JSON de la SA va **solo** como secret de CI o en `.env` local.
