# SECURITY.md — Plan de hardening de CI (en revisión)

Plan accionable de seguridad para los workflows de GitHub Actions, surgido de una
revisión cruzada (Claude vs. GLM). **Estado: pendiente de 3 decisiones** (ver §4)
antes de ejecutar. Para arquitectura y convenciones, ver [AGENTS.md](AGENTS.md);
para despliegue, [DEPLOY.md](DEPLOY.md).

---

## 0. Contexto verificado (no re-litigar)

Hechos comprobados contra el repo en la sesión de revisión:

- **Ningún workflow declara `permissions:`** → el `GITHUB_TOKEN` hereda el default
  del repo (posiblemente read-write). `regenerate.yml` y `audit.yml` no tienen
  bloque `permissions:`.
- **`actions/checkout@v4` sin `persist-credentials: false`** → el token queda en
  `.git/config`. El despliegue va por SSH (`DEPLOY_KEY`); el token de git no se
  necesita nunca.
- **`pypa/gh-action-pip-audit@v1`** (`audit.yml:42`) anclado a tag **mutable**.
- **`ignore-vulns: ""`** (`audit.yml:45`) con comentario que dice "ignoramos CVEs
  sin parche" — **falso**: la lista vacía no ignora nada. El comportamiento real
  (no ignorar nada) es el correcto; el comentario miente.
- **`requirements.txt` usa `>=`** (pins flotantes) → CI instala lo último
  compatible en el mismo runner que luego ve `DEPLOY_KEY`/`LLM_API_KEY`/`X_*`.

Dos puntos que se aclararon en la revisión (importante no volver a equivocarse):

- **Dependabot YA está configurado.** Existe `.github/dependabot.yml` (commit
  `ad4e855`) con dos ecosystems: `pip` (l. 12) y `github-actions` (l. 25).
  → Un pin SHA de una action lo mantiene Dependabot solo; **no hace falta crear
  nada**. Ojo: *version updates* (el `.yml`) ≠ *security alerts* (toggle de
  Settings): son features separadas; el toggle de alertas aún hay que activarlo.
- **`actions/cache@v4` NO usa el `GITHUB_TOKEN`.** Se autentica con un *runtime
  token* aparte que inyecta el runner, no afectado por el bloque `permissions:`.
  → `contents: read` solo **alcanza para salvar caché**. **No añadir**
  `actions: write` "por si acaso": aflojaría el privilegio en el PR que busca lo
  contrario. (Fuente: README oficial de `actions/cache`, sección "Read-only
  access".)

**Límite honesto de Tier A:** `contents: read` cierra el vector de *escritura al
repo* vía `GITHUB_TOKEN`, pero **no** la cadena de suministro hacia los secrets de
verdad. El paso *“Generar sitio”* (`regenerate.yml:123-134`) corre Python con
`LLM_API_KEY`/`X_BEARER_TOKEN`/`BLUESKY_*` en `env` y `~/.ssh/id_deploy` en disco;
una dependencia comprometida puede ejecutar código al importarse (`.pth`, hooks)
con esos secrets accesibles vía `os.environ`. Eso es lo que mitiga Tier C (§3).

---

## 1. Fase 1 — En-repo, un PR (Tier A + Tier B + Nit)

### `regenerate.yml`

**(a)** Insertar `permissions:` entre el bloque `concurrency:` (l. 24) y `jobs:` (l. 26):

```yaml
# Endurece el GITHUB_TOKEN: solo lectura al repo. El despliegue va por SSH
# (DEPLOY_KEY), nunca via token de git. actions/cache se autentica con un
# runtime token aparte que inyecta el runner, así que NO hace falta
# actions: write para salvar caché.
permissions:
  contents: read
```

**(b)** `persist-credentials: false` en el checkout (l. 36-37):

```yaml
      - name: Checkout
        uses: actions/checkout@v4
        with:
          persist-credentials: false
```

### `audit.yml`

**(c)** Insertar `permissions:` antes de `jobs:` (l. 26):

```yaml
permissions:
  contents: read
```

**(d)** `persist-credentials: false` en el checkout (l. 30-31) — igual que (b).

**(e)** Pin del action de terceros a SHA (l. 42). SHA verificado del release
`v1.1.0` (commit firmado con GPG, fuente: página de releases del repo):

```yaml
        uses: pypa/gh-action-pip-audit@1220774d901786e6f652ae159f7b6bc8fea6d266  # v1.1.0
```

Dependabot (ecosystem `github-actions`) propondrá el bump cuando salga una versión
nueva.

**(f)** Arreglar el comentario mentiroso (l. 44-46):

```yaml
        with:
          # Solo auditamos el árbol instalado (lo que realmente se ejecuta en CI
          # y en producción). NO ignoramos ningún CVE (ni los sin parche): los
          # queremos ver como alertas, aunque algunos no tengan fix todavía.
          ignore-vulns: ""
```
(Opción: borrar la línea `ignore-vulns: ""`, que es el default. Ver §4.)

### Verificación post-merge (gratis)

`regenerate.yml` corre a diario → en el primer run tras mergear, confirmar que el
paso *“Restaurar cache de traducciones”* (`actions/cache@v4`) guarda sin error de
permisos. Si apareciera (no debería, según el README), se añadiría `actions: write`
solo entonces. No pre-concederlo.

---

## 2. Fase 2 — Settings (manual, no entra en el PR)

Requiere acceso de owner al repo. No se puede hacer por PR; va la ruta de clics:

1. **Settings → Code security** → activar:
   - **Push protection** (bloquea pushes con secrets antes de que aterricen). Gratis
     en repo público.
   - **Secret scanning alerts**.
   - **Dependabot security alerts** (distinto de los PRs de versión que ya da el
     `.github/dependabot.yml`).
2. *(Opcional)* **CodeQL** → "Default setup". Gratis en público.

---

## 3. Fase 3 — Tier C: hash-pin de dependencias (PR separado, con decisión)

Fase con trade-offs reales; por eso va aparte y requiere visto bueno (§4).

**Qué hacer:**

1. Añadir `pip-tools` a `requirements-dev.txt`.
2. Mantener `requirements.txt` como **manifest** editable (Dependabot lo sigue
   tocando; queda con `>=`).
3. Generar `requirements.lock` con hashes:
   `pip-compile --generate-hashes --output-file=requirements.lock requirements.txt`.
4. CI instala desde el lock: `pip install -r requirements.lock --require-hashes` en
   los pasos de install de `regenerate.yml` (l. 45) y `audit.yml` (l. 39).

**Dos avisos honestos:**

- **Plataforma del lock:** `pip-compile` resuelve wheels para la plataforma donde
  corre. CI es `ubuntu-latest`; la máquina de desarrollo es Windows. Un lock
  generado en Windows puede hacer fallar `--require-hashes` en CI por wheels
  distintos. → Generar el lock en **linux** (container, o un workflow ad-hoc que
  corra `pip-compile` y comite el resultado).
- **Fricción con Dependabot:** cada PR `deps(pip)` deja el lock desfasado →
  `audit.yml` (que corre en PRs que tocan `requirements.txt`) fallará hasta que se
  regenere el lock. Ese fallo *ruidoso es la propiedad de seguridad* (fuerza a
  reconciliar), pero es mantenimiento recurrente.

**Recomendación de la sesión:** si el modelo de amenazas no subió (proyecto
personal, sin secrets de prod sensibles más allá de los de CI), **diferir Tier C** y
conformarse con Dependabot security alerts (Fase 2) como mitigación barata del
80/20. Si se quiere el lock igual, implementarlo con un workflow de regeneración en
CI para evitar el problema de plataforma.

---

## 4. Decisiones pendientes (retomar en otra sesión)

Antes de ejecutar, confirmar:

1. **Fase 3 (Tier C):** ¿se implementa ahora (con workflow CI que genere el lock en
   linux) o se **difiere**? *Recomendación de la sesión: diferir.*
2. **`ignore-vulns: ""`** (`audit.yml:45`): ¿se deja con el comentario arreglado, o
   se borra la línea (es el default)?
3. **Estructura del PR de Fase 1:** ¿un solo PR con todo (Tier A + pin SHA + nit),
   o Tier A separado del (pin SHA + nit)?

Resueltas esas tres, Fase 1 se puede aplicar y commitear directamente (más Fase 3 si
se aprueba). Fase 2 es manual en Settings y no depende de este repo.
