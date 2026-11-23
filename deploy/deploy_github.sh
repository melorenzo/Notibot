#!/usr/bin/env bash
# deploy_github.sh — lleva el deploy al server usando GitHub como transporte.
#
# Corre DENTRO del server. No hace falta scp ni copiar archivos a mano.
#
#   bash deploy/deploy_github.sh              # validar (no toca producción)
#   bash deploy/deploy_github.sh --apply      # pasar a producción
#   bash deploy/deploy_github.sh --rollback   # volver al backup más reciente
#
# Qué hace en modo --check:
#   1. clona (o actualiza) el repo en app2-notibot-nuevo
#   2. copia el estado del directorio actual: variables.env, data/, logs/,
#      token_jwt/, keys/ y config/  (nunca al revés, nunca borra)
#   3. construye la imagen nueva y verifica Python 3.12 + scrapling
#   4. chequea variables requeridas
#   5. corre las fuentes HTML y cuenta items
#
# En modo --apply, si todo lo anterior pasó, hace el swap con backup y
# recrea solamente los contenedores del notibot.
set -uo pipefail

REPO_URL="${REPO_URL:-https://github.com/melorenzo/Notibot.git}"
BRANCH="${BRANCH:-main}"
COMPOSE_DIR="${COMPOSE_DIR:-/home/mlorenzo/docker}"
PROD_DIR="${PROD_DIR:-$COMPOSE_DIR/app2-notibot-V2}"
WORK_DIR="${WORK_DIR:-$COMPOSE_DIR/app2-notibot-nuevo}"
STAMP="$(date +%Y%m%d-%H%M%S)"

MODO="check"
case "${1:-}" in
  ""|--check) MODO="check" ;;
  --apply)    MODO="apply" ;;
  --rollback) MODO="rollback" ;;
  -h|--help)  sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "Uso: $0 [--check|--apply|--rollback]"; exit 2 ;;
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

# ─────────────────────────────────────────────────────────── rollback ──
if [ "$MODO" = "rollback" ]; then
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
aviso "prueba     : $WORK_DIR"
aviso "compose    : $COMPOSE_DIR"
aviso "servicios  : $(servicios)"

# ─────────────────────────────────────────────── 1. traer el código ──
msg "1. Código desde GitHub ($BRANCH)"
if [ -d "$WORK_DIR/.git" ]; then
  git -C "$WORK_DIR" fetch --quiet origin "$BRANCH" || fallar "no pude hacer fetch"
  git -C "$WORK_DIR" checkout --quiet "$BRANCH" || fallar "no pude cambiar a $BRANCH"
  git -C "$WORK_DIR" reset --hard --quiet "origin/$BRANCH" || fallar "no pude actualizar el código"
  aviso "actualizado"
else
  [ -e "$WORK_DIR" ] && fallar "$WORK_DIR existe y no es un repo git: movelo a mano y volvé a correr"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$WORK_DIR" || fallar "no pude clonar $REPO_URL"
  aviso "clonado"
fi
SHA=$(git -C "$WORK_DIR" rev-parse --short HEAD)
aviso "commit: $SHA"

# ───────────────────────────────────────── 2. estado y credenciales ──
msg "2. Estado del directorio actual"
if [ -f "$WORK_DIR/variables.env" ]; then
  aviso "variables.env: ya estaba en el directorio de prueba, lo dejo"
else
  cp "$PROD_DIR/variables.env" "$WORK_DIR/variables.env" || fallar "no pude copiar variables.env"
  chmod 600 "$WORK_DIR/variables.env"
  aviso "variables.env: copiado (600)"
fi
for d in data logs token_jwt keys; do
  copiar_dir "$PROD_DIR/$d/" "$WORK_DIR/$d/"
  [ -d "$PROD_DIR/$d" ] && aviso "$d/: copiado"
done
copiar_dir "$PROD_DIR/config/" "$WORK_DIR/config/"
aviso "config/: copiado (diarios.json y estado del panel del server mandan)"
if [ -f "$WORK_DIR/config/secrets.env" ]; then chmod 600 "$WORK_DIR/config/secrets.env"; fi
# no arrastrar salidas de pruebas viejas
rm -f "$WORK_DIR"/salidas_*.json 2>/dev/null || true

# ───────────────────────────────────────────────────── 3. imagen ──
msg "3. Imagen (Python 3.12 + scrapling)"
IMG="notibot:deploy-$SHA"
docker build -q -t "$IMG" "$WORK_DIR" >/dev/null || fallar "falló el docker build"
docker run --rm "$IMG" python -c "import sys, scrapling; print('    python ' + sys.version.split()[0] + ' | scrapling ' + scrapling.__version__)" \
  || fallar "la imagen no importa scrapling"

# ─────────────────────────────────────── 4. variables requeridas ──
msg "4. Variables requeridas"
docker run --rm -i -v "$WORK_DIR:/app" --env-file "$WORK_DIR/variables.env" "$IMG" python - <<'PY' || FALTAN=1
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
  docker run --rm -v "$WORK_DIR:/app" --env-file "$WORK_DIR/variables.env" \
    -e SCRAPER_DEBUG=1 "$IMG" sh -c "cd /app && python scraping/scraper_$s.py" >/dev/null 2>&1
  ARCHIVO=$(basename "$(grep -E "^$VAR=" "$WORK_DIR/variables.env" 2>/dev/null | head -1 | cut -d= -f2- || true)")
  [ -n "$ARCHIVO" ] || ARCHIVO="salidas_$s.json"
  N=$(docker run --rm -v "$WORK_DIR:/app" "$IMG" python -c "
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
  aviso "Si el panel no abre, falta PANEL_PASSWORD en variables.env"
  exit 0
fi

[ -z "$FALLAS" ] || fallar "no aplico: fuentes sin items ->$FALLAS"

msg "6. Swap a producción"
VIEJO="$COMPOSE_DIR/app2-notibot-V2.bak-$STAMP"
IMG_COMPOSE=$(imagen_compose)
if [ -n "$IMG_COMPOSE" ]; then
  docker tag "$IMG" "$IMG_COMPOSE" && aviso "imagen $IMG_COMPOSE actualizada"
fi

mv "$PROD_DIR" "$VIEJO" || fallar "no pude mover producción a $VIEJO"
if ! mv "$WORK_DIR" "$PROD_DIR"; then
  mv "$VIEJO" "$PROD_DIR"
  fallar "no pude mover el directorio nuevo; producción quedó intacta"
fi

revertir() {
  echo "    revirtiendo..."
  mv "$PROD_DIR" "$COMPOSE_DIR/app2-notibot-V2.fallado-$STAMP" 2>/dev/null || true
  mv "$VIEJO" "$PROD_DIR" 2>/dev/null || true
  compose up -d --force-recreate $(servicios) >/dev/null 2>&1 || true
  echo "    producción restaurada; el directorio fallado quedó en app2-notibot-V2.fallado-$STAMP"
}

if ! compose up -d --force-recreate $(servicios); then
  revertir
  fallar "falló la recreación de contenedores"
fi
sleep 8
compose ps 2>/dev/null | grep -i notibot || true

msg "7. Smoke test dentro del contenedor"
if ! bash "$PROD_DIR/deploy/smoke_test.sh"; then
  aviso "el smoke test reportó fallas"
  aviso "rollback:  bash $0 --rollback"
  exit 1
fi

msg "Listo. Producción corre el commit $SHA"
aviso "backup del código anterior: $VIEJO"
aviso "rollback:  bash $0 --rollback"
