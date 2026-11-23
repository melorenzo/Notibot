#!/usr/bin/env bash
# deploy_github.sh — lleva el deploy al server usando GitHub como transporte.
#
# Corre DENTRO del server. No hace falta scp ni copiar archivos a mano.
#
#   bash deploy/deploy_github.sh              # validar (no toca producción)
#   bash deploy/deploy_github.sh --diff       # ver qué cambia el reset con git
#   bash deploy/deploy_github.sh --apply      # pasar a producción
#   bash deploy/deploy_github.sh --rollback   # volver al backup más reciente
#
# Dos escenarios:
#   - en sitio: app2-notibot-V2 ya es el clon git. Se actualiza con git, sin
#     swap; el rollback es git reset al commit anterior.
#   - clon aparte: si producción no es git, clona en app2-notibot-nuevo y hace
#     el swap con backup.
#
# Qué hace en modo --check:
#   1. trae el código (git o clone)
#   2. copia el estado del directorio actual: variables.env, data/, logs/,
#      token_jwt/, keys/ y config/  (nunca al revés, nunca borra) y completa
#      las PANEL_* que falten
#   3. construye la imagen nueva y verifica Python 3.12 + scrapling
#   4. chequea variables requeridas
#   5. corre las fuentes HTML y cuenta items
#
# En modo --apply, si todo lo anterior pasó, recrea los contenedores del
# notibot, deja el panel escuchando en 127.0.0.1:8030 y corre el smoke test.
set -uo pipefail

REPO_URL="${REPO_URL:-https://github.com/melorenzo/Notibot.git}"
BRANCH="${BRANCH:-main}"
COMPOSE_DIR="${COMPOSE_DIR:-/home/mlorenzo/docker}"
PROD_DIR="${PROD_DIR:-$COMPOSE_DIR/app2-notibot-V2}"
WORK_DIR="${WORK_DIR:-$COMPOSE_DIR/app2-notibot-nuevo}"
STAMP="$(date +%Y%m%d-%H%M%S)"
PREV=""

MODO="check"
case "${1:-}" in
  ""|--check) MODO="check" ;;
  --apply)    MODO="apply" ;;
  --rollback) MODO="rollback" ;;
  --diff)     MODO="diff" ;;
  -h|--help)  sed -n '2,30p' "$0"; exit 0 ;;
  *) echo "Uso: $0 [--check|--diff|--apply|--rollback]"; exit 2 ;;
esac

msg()    { printf '\n==> %s\n' "$*"; }
aviso()  { printf '    %s\n' "$*"; }
fallar() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

compose() { ( cd "$COMPOSE_DIR" && docker compose "$@" ); }

servicios() {
  local s
  s=$( compose config --services 2>/dev/null | grep -i notibot | tr '\n' ' ' || true )
  [ -n "$s" ] || s="app2-notibot notibot-cron-a notibot-cron-b notibot-cron-c notibot-cron-d"
  echo "$s"
}

imagen_compose() {
  compose config --images 2>/dev/null | grep -i notibot | head -1 || true
}

aleatorio() { # $1 = bytes
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "${1:-16}"
  else
    head -c "${1:-16}" /dev/urandom | od -An -tx1 | tr -d ' \n'
  fi
}

asegurar_panel_env() { # $1 = variables.env
  local f="$1" agregue=0
  [ -f "$f" ] || return 1
  grep -q '^PANEL_SECRET=' "$f" || {
    printf 'PANEL_SECRET=%s\n' "$(aleatorio 32)" >> "$f"; aviso "PANEL_SECRET: generada"; agregue=1; }
  if ! grep -q '^PANEL_PASSWORD=' "$f"; then
    PANEL_GENERADA=$(aleatorio 9)
    printf 'PANEL_PASSWORD=%s\n' "$PANEL_GENERADA" >> "$f"
    aviso "PANEL_PASSWORD: generada -> $PANEL_GENERADA (login del panel)"
    agregue=1
  fi
  grep -q '^PANEL_TRUST_PROXY=' "$f"  || { echo 'PANEL_TRUST_PROXY=1'  >> "$f"; agregue=1; }
  grep -q '^PANEL_COOKIE_SECURE=' "$f" || { echo 'PANEL_COOKIE_SECURE=1' >> "$f"; agregue=1; }
  [ "$agregue" = 1 ] && chmod 600 "$f" 2>/dev/null || true
  return 0
}

recrear_panel() { # $1 = imagen, $2 = directorio del proyecto
  local img="$1" dir="$2"
  docker rm -f notibot-panel >/dev/null 2>&1 || true
  docker run -d --name notibot-panel --restart unless-stopped \
    --env-file "$dir/variables.env" -v "$dir:/app" -w /app \
    -p 127.0.0.1:8030:8030 "$img" \
    uvicorn panel.app:app --host 0.0.0.0 --port 8030 >/dev/null 2>&1 || {
      aviso "no pude levantar notibot-panel (docker logs notibot-panel)"
      return 1
    }
  return 0
}

codigo_panel() {
  local c
  c=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        http://127.0.0.1:8030/login 2>/dev/null) || true
  [ -n "$c" ] || c=000
  echo "$c"
}

info_panel() {
  local c; c=$(codigo_panel)
  case "$c" in
    200) aviso "panel      : http://127.0.0.1:8030/login responde 200" ;;
    *)   aviso "panel      : http://127.0.0.1:8030/login -> $c (docker logs notibot-panel)" ;;
  esac
}

copiar_dir() { # origen con barra final, destino con barra final
  local src="$1" dst="$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dst"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a "$src" "$dst"
  else
    cp -a "$src." "$dst" 2>/dev/null || cp -a "$src"* "$dst"
  fi
}

# ── modo en sitio ──────────────────────────────────────────────────────────
# Si producción ya es un clon del repo (el directorio viejo se movió a
# app2-notibot-v2-viejo), no hay clon aparte ni swap: se actualiza con git y se
# recrean los servicios. Los contenedores viejos siguen montados sobre el
# directorio renombrado, así que el reset no los toca.
EN_SITIO=0
DIR_TRABAJO="$WORK_DIR"
if [ -d "$PROD_DIR/.git" ]; then
  EN_SITIO=1
  DIR_TRABAJO="$PROD_DIR"
fi

# ─────────────────────────────────────────────────────────────── diff ──
# Qué se llevaría puesto el git reset: archivos versionados con cambios
# locales (se descartan) y archivos que no están en el repo (sobreviven).
if [ "$MODO" = "diff" ]; then
  [ "$EN_SITIO" = 1 ] || fallar "$PROD_DIR no es un clon git; no hay nada que diferenciar"
  ENCABEZADO=$(git -C "$PROD_DIR" rev-parse --short HEAD)
  MODIFICADOS=$(git -C "$PROD_DIR" status --porcelain | grep -v '^??' || true)
  SUELTOS=$(git -C "$PROD_DIR" status --porcelain | grep '^??' || true)
  msg "Diff contra HEAD ($ENCABEZADO)"
  if [ -n "$MODIFICADOS" ]; then
    aviso "versionados con cambios locales (los descarta el reset):"
    printf '%s\n' "$MODIFICADOS" | sed 's/^/      /'
  else
    aviso "sin cambios en archivos versionados"
  fi
  if [ -n "$SUELTOS" ]; then
    aviso "fuera del repo (no los toca):"
    printf '%s\n' "$SUELTOS" | sed 's/^/      /'
  fi
  PATCH="$COMPOSE_DIR/notibot-antes-$STAMP.patch"
  git -C "$PROD_DIR" diff > "$PATCH" 2>/dev/null || true
  aviso "diff completo: $PATCH ($(wc -l < "$PATCH" 2>/dev/null || echo 0) líneas)"
  exit 0
fi

# ─────────────────────────────────────────────────────────── rollback ──
if [ "$MODO" = "rollback" ]; then
  if [ "$EN_SITIO" = 1 ] && [ -f "$COMPOSE_DIR/.notibot-prev-sha" ]; then
    PREV=$(cat "$COMPOSE_DIR/.notibot-prev-sha")
    msg "Volviendo el código en sitio a ${PREV:0:7}"
    git -C "$PROD_DIR" reset --hard --quiet "$PREV" || fallar "no pude volver a $PREV"
    IMG_COMPOSE=$(imagen_compose)
    [ -n "$IMG_COMPOSE" ] || IMG_COMPOSE="app2-notibot:latest"
    docker build -q -t "$IMG_COMPOSE" "$PROD_DIR" >/dev/null || fallar "falló el docker build del rollback"
    compose up -d --force-recreate $(servicios) || fallar "no pude recrear los contenedores"
    compose config --services 2>/dev/null | grep -qx "notibot-panel" || recrear_panel "$IMG_COMPOSE" "$PROD_DIR" || true
    msg "Rollback listo: $(git -C "$PROD_DIR" rev-parse --short HEAD)"
    exit 0
  fi
  ULTIMO=$(ls -1d "$COMPOSE_DIR"/app2-notibot-V2.bak-* 2>/dev/null | sort | tail -1)
  [ -n "$ULTIMO" ] || fallar "no hay backups app2-notibot-V2.bak-* en $COMPOSE_DIR"
  msg "Volviendo a $(basename "$ULTIMO")"
  mv "$PROD_DIR" "$COMPOSE_DIR/app2-notibot-V2.fallado-$STAMP" || fallar "no pude mover el directorio actual"
  mv "$ULTIMO" "$PROD_DIR" || fallar "no pude restaurar el backup"
  compose up -d --force-recreate $(servicios) || fallar "no pude recrear los contenedores"
  msg "Rollback listo. El directorio que falló quedó en app2-notibot-V2.fallado-$STAMP"
  exit 0
fi

# ────────────────────────────────────────────────────── 0. preflight ──
msg "0. Preflight"
command -v docker >/dev/null 2>&1 || fallar "no encuentro docker"
command -v git    >/dev/null 2>&1 || fallar "no encuentro git"
compose version >/dev/null 2>&1 || fallar "no encuentro docker compose (plugin)"
[ -f "$PROD_DIR/cron_base.py" ] || fallar "$PROD_DIR no parece el proyecto del notibot"
[ -d "$COMPOSE_DIR" ] || fallar "no existe $COMPOSE_DIR"
aviso "producción : $PROD_DIR"
if [ "$EN_SITIO" = 1 ]; then
  aviso "modo       : en sitio (el repo ya está en producción, sin swap)"
  aviso "estado     : se queda donde está (variables.env, data/, config/)"
else
  aviso "prueba     : $WORK_DIR"
fi
aviso "compose    : $COMPOSE_DIR"
aviso "servicios  : $(servicios)"
if compose config --services 2>/dev/null | grep -qx "notibot-panel"; then
  aviso "panel      : notibot-panel -> http://127.0.0.1:8030 (servicio del compose)"
else
  aviso "panel      : no está en el compose; lo levanto con docker run en 127.0.0.1:8030"
fi

# ─────────────────────────────────────────────── 1. traer el código ──
msg "1. Código desde GitHub ($BRANCH)"
if [ "$EN_SITIO" = 1 ]; then
  PREV=$(git -C "$PROD_DIR" rev-parse HEAD)
  # guardar lo que el reset pueda pisar, antes de tocar nada
  RESPALDO="$COMPOSE_DIR/notibot-antes-$STAMP"
  git -C "$PROD_DIR" status --porcelain > "$RESPALDO.status" 2>/dev/null || true
  git -C "$PROD_DIR" diff > "$RESPALDO.patch" 2>/dev/null || true
  N_MOD=$(grep -c -v '^??' "$RESPALDO.status" 2>/dev/null || true)
  [ -n "$N_MOD" ] || N_MOD=0
  if [ "$N_MOD" -gt 0 ] 2>/dev/null; then
    aviso "OJO: $N_MOD archivo(s) versionados con cambios locales; el reset los descarta"
  fi
  aviso "estado previo guardado en $(basename "$RESPALDO").{status,patch}"
  git -C "$PROD_DIR" fetch --quiet origin "$BRANCH" || fallar "no pude hacer fetch"
  git -C "$PROD_DIR" checkout --quiet "$BRANCH" || fallar "no pude cambiar a $BRANCH"
  git -C "$PROD_DIR" reset --hard --quiet "origin/$BRANCH" || fallar "no pude actualizar el código"
  aviso "antes: ${PREV:0:7}"
elif [ -d "$WORK_DIR/.git" ]; then
  git -C "$WORK_DIR" fetch --quiet origin "$BRANCH" || fallar "no pude hacer fetch"
  git -C "$WORK_DIR" checkout --quiet "$BRANCH" || fallar "no pude cambiar a $BRANCH"
  git -C "$WORK_DIR" reset --hard --quiet "origin/$BRANCH" || fallar "no pude actualizar el código"
  aviso "actualizado"
else
  [ -e "$WORK_DIR" ] && fallar "$WORK_DIR existe y no es un repo git: movelo a mano y volvé a correr"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$WORK_DIR" || fallar "no pude clonar $REPO_URL"
  aviso "clonado"
fi
SHA=$(git -C "$DIR_TRABAJO" rev-parse --short HEAD)
aviso "commit: $SHA"

# ───────────────────────────────────────── 2. estado y credenciales ──
msg "2. Estado del directorio actual"
if [ "$EN_SITIO" = 1 ]; then
  # el estado ya vive acá: nunca se copia ni se pisa
  [ -f "$PROD_DIR/variables.env" ] || fallar "falta $PROD_DIR/variables.env: copiala del directorio viejo antes de seguir"
  [ -f "$PROD_DIR/data/used_items.json" ] || aviso "OJO: no veo data/used_items.json (sin historial se republica todo)"
  aviso "estado: queda como está (variables.env, data/, logs/, token_jwt/, keys/, config/)"
else
  if [ -f "$WORK_DIR/variables.env" ]; then
    aviso "variables.env: ya estaba en el clon, lo dejo"
  else
    [ -f "$PROD_DIR/variables.env" ] || fallar "no encuentro $PROD_DIR/variables.env"
    cp "$PROD_DIR/variables.env" "$WORK_DIR/variables.env" || fallar "no pude copiar variables.env"
    aviso "variables.env: copiado de producción"
  fi
  for d in data logs token_jwt keys; do
    copiar_dir "$PROD_DIR/$d/" "$WORK_DIR/$d/"
    [ -d "$PROD_DIR/$d" ] && aviso "$d/: copiado"
  done
  copiar_dir "$PROD_DIR/config/" "$WORK_DIR/config/"
  aviso "config/: copiado (diarios.json y estado del panel del server mandan)"
  # no arrastrar salidas de pruebas viejas
  rm -f "$WORK_DIR"/salidas_*.json 2>/dev/null || true
fi
chmod 600 "$DIR_TRABAJO/variables.env" 2>/dev/null || true
if [ -f "$DIR_TRABAJO/config/secrets.env" ]; then chmod 600 "$DIR_TRABAJO/config/secrets.env"; fi
asegurar_panel_env "$DIR_TRABAJO/variables.env" || aviso "OJO: no pude leer $DIR_TRABAJO/variables.env"

# ───────────────────────────────────────────────────── 3. imagen ──
msg "3. Imagen (Python 3.12 + scrapling)"
IMG="notibot:deploy-$SHA"
docker build -q -t "$IMG" "$DIR_TRABAJO" >/dev/null || fallar "falló el docker build"
docker run --rm "$IMG" python -c "import sys, scrapling; print('    python ' + sys.version.split()[0] + ' | scrapling ' + scrapling.__version__)" \
  || fallar "la imagen no importa scrapling"

# ─────────────────────────────────────── 4. variables requeridas ──
msg "4. Variables requeridas"
docker run --rm -i -v "$DIR_TRABAJO:/app" --env-file "$DIR_TRABAJO/variables.env" "$IMG" python - <<'PY' || FALTAN=1
import json, os, sys

faltan = []
for v in ("PANEL_PASSWORD", "TELEGRAM_BOT_TOKEN", "GROQ_API_KEY"):
    if not (os.getenv(v) or "").strip():
        faltan.append(v)

try:
    diarios = json.load(open("/app/config/diarios.json", encoding="utf-8"))
except Exception:
    diarios = {}

for slug, cfg in sorted(diarios.items()):
    if not isinstance(cfg, dict) or cfg.get("activo") is False:
        continue
    for campo in ("username_env", "password_env"):
        var = (cfg.get(campo) or "").strip()
        if var and not (os.getenv(var) or "").strip():
            faltan.append(var + " (" + slug + ")")

if faltan:
    print("    FALTAN: " + ", ".join(sorted(set(faltan))))
    print("    -> el panel no deja entrar sin PANEL_PASSWORD; el resto son diarios que no van a publicar")
    sys.exit(1)
print("    todas presentes")
PY

# ──────────────────────────────────────── 5. fuentes HTML ──
msg "5. Fuentes HTML (conteo de items)"
FALLAS=""
for s in ba_static ba_noticias lanacion infobae; do
  case "$s" in
    ba_static)   VAR=SALIDAS_BA_STATIC ;;
    ba_noticias) VAR=SALIDAS_BA_NOTICIAS ;;
    lanacion)    VAR=SALIDAS_LANACION ;;
    infobae)     VAR=SALIDAS_INFOBAE ;;
  esac
  docker run --rm -v "$DIR_TRABAJO:/app" --env-file "$DIR_TRABAJO/variables.env" \
    -e SCRAPER_DEBUG=1 "$IMG" sh -c "cd /app && python scraping/scraper_$s.py" >/dev/null 2>&1
  ARCHIVO=$(basename "$(grep -E "^$VAR=" "$DIR_TRABAJO/variables.env" 2>/dev/null | head -1 | cut -d= -f2- || true)")
  [ -n "$ARCHIVO" ] || ARCHIVO="salidas_$s.json"
  N=$(docker run --rm -v "$DIR_TRABAJO:/app" "$IMG" python -c "
import json, sys
try:
    d = json.load(open('/app/$ARCHIVO', encoding='utf-8')) or []
    print(len(d))
except Exception:
    print(0)
" 2>/dev/null)
  [ -n "$N" ] || N=0
  if [ "$N" -gt 0 ]; then
    printf '    [OK   ] %-12s %3s items\n' "$s" "$N"
  else
    printf '    [FALLA] %-12s %3s items\n' "$s" "$N"
    FALLAS="$FALLAS $s"
  fi
done
aviso "equipoba no corre acá (necesita el contenedor de Selenium); se valida con el smoke test del cron"

# ───────────────────────────────────────────────────────── apply ──
if [ "$MODO" = "check" ]; then
  msg "Validación terminada (no toqué producción)"
  aviso "Si las fuentes están OK:  bash $0 --apply"
  aviso "El panel se levanta en el --apply: http://127.0.0.1:8030/login"
  exit 0
fi

[ -z "$FALLAS" ] || fallar "no aplico: fuentes sin items ->$FALLAS"

VIEJO=""
if [ "$EN_SITIO" = 1 ]; then
  msg "6. Recrear los servicios (en sitio, sin swap)"
  printf '%s\n' "$PREV" > "$COMPOSE_DIR/.notibot-prev-sha"
  aviso "commit anterior anotado: ${PREV:0:7} (--rollback vuelve a ese)"
else
  msg "6. Swap a producción"
  VIEJO="$COMPOSE_DIR/app2-notibot-V2.bak-$STAMP"
  mv "$PROD_DIR" "$VIEJO" || fallar "no pude mover producción a $VIEJO"
  if ! mv "$WORK_DIR" "$PROD_DIR"; then
    mv "$VIEJO" "$PROD_DIR"
    fallar "no pude mover el directorio nuevo; producción quedó intacta"
  fi
fi

IMG_COMPOSE=$(imagen_compose)
if [ -n "$IMG_COMPOSE" ]; then
  docker tag "$IMG" "$IMG_COMPOSE" && aviso "imagen $IMG_COMPOSE actualizada"
fi

revertir() {
  echo "    revirtiendo..."
  if [ "$EN_SITIO" = 1 ]; then
    git -C "$PROD_DIR" reset --hard --quiet "$PREV" 2>/dev/null || true
    docker build -q -t "$IMG_COMPOSE" "$PROD_DIR" >/dev/null 2>&1 || true
    echo "    código de vuelta en ${PREV:0:7}"
  else
    mv "$PROD_DIR" "$COMPOSE_DIR/app2-notibot-V2.fallado-$STAMP" 2>/dev/null || true
    mv "$VIEJO" "$PROD_DIR" 2>/dev/null || true
    echo "    producción restaurada; el directorio fallado quedó en app2-notibot-V2.fallado-$STAMP"
  fi
  compose up -d --force-recreate $(servicios) >/dev/null 2>&1 || true
}

if ! compose up -d --force-recreate $(servicios); then
  revertir
  fallar "falló la recreación de contenedores"
fi
sleep 8
compose ps 2>/dev/null | grep -i notibot || true

if compose config --services 2>/dev/null | grep -qx "notibot-panel"; then
  info_panel
elif recrear_panel "$IMG" "$PROD_DIR"; then
  sleep 3
  aviso "panel      : contenedor notibot-panel creado fuera del compose"
  info_panel
fi

msg "7. Smoke test dentro del contenedor"
if ! bash "$PROD_DIR/deploy/smoke_test.sh"; then
  aviso "el smoke test reportó fallas"
  aviso "rollback:  bash $0 --rollback"
  exit 1
fi

msg "Listo. Producción corre el commit $SHA"
if [ "$EN_SITIO" = 1 ]; then
  aviso "rollback:  bash $0 --rollback   (vuelve a ${PREV:0:7})"
else
  aviso "backup del código anterior: $VIEJO"
  aviso "rollback:  bash $0 --rollback"
fi
