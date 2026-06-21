# Opciones SSH y helper de reintentos con backoff, compartido por los pasos de
# descarga y subida del workflow de regeneración. Se incorpora con `source`.
#
# No es ejecutable por sí solo: solo define SSH_OPTS, RETRIES y la función
# reintentar(). La clave privada (~/.ssh/id_deploy) la prepara el workflow.

RETRIES=5
SSH_OPTS="-o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

# reintentar <descripción> <comando...>
# Ejecuta el comando; ante fallo reintenta con backoff exponencial
# (10s, 20s, 40s, 80s; tope 90s). Absorbe timeouts y baneos transitorios de
# fail2ban y las IP rotatorias de los runners. En el camino feliz el comando
# entra al primer intento y no añade ninguna espera.
reintentar() {
  local desc="$1"; shift
  local demora=10 n=1
  while true; do
    if "$@"; then return 0; fi
    if [ "$n" -ge "$RETRIES" ]; then
      echo "✗ $desc: falló tras $RETRIES intentos." >&2
      return 1
    fi
    echo "  ⟳ $desc: intento $n/$RETRIES falló; reintento en ${demora}s..." >&2
    sleep "$demora"
    n=$((n + 1)); demora=$((demora * 2))
    if [ "$demora" -gt 90 ]; then demora=90; fi
  done
}
