#!/usr/bin/env bash
# deploy.sh — corre DENTRO del server.
#
# Uso:
#   bash /home/mlorenzo/docker/notibot-patch-20261103/deploy/deploy.sh \
#        /home/mlorenzo/docker/notibot-patch-20261103
#
# Qué hace:
#   1. Backup real de los archivos que va a pisar (incluye variables.env).
#   2. Diff de variables.env para que lo revises antes de aplicarlo.
#   3. Copia los archivos nuevos.
#   4. Reinicia los contenedores (el código está montado como volumen, así
#      que no hace falta rebuild de la imagen).
#   5. Corre el smoke test (no publica nada).
#
# Flags:
#   --no-env   no sobreescribir variables.env (solo el código)
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/home/mlorenzo/docker/app2-notibot-V2}"
COMPOSE_DIR="${COMPOSE_DIR:-/home/mlorenzo/docker}"
CONTAINER="${CONTAINER:-notibot-cron-a}"
PATCH_DIR="${1:-}"
COPY_ENV=1
[ "${2:-}" = "--no-env" ] && COPY_ENV=0

if [ -z "$PATCH_DIR" ] || [ ! -d "$PATCH_DIR" ]; then
  echo "ERROR: falta la carpeta del patch."
  echo "Uso: bash deploy.sh /ruta/al/notibot-patch-XXXX [--no-env]"
  exit 1
fi

cd "$PROJECT_DIR"
[ -f cron_base.py ] || { echo "ERROR: $PROJECT_DIR no parece el proyecto"; exit 1; }

STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$PROJECT_DIR/deploy/backup/$STAMP"

echo "======================================================================"
echo " DEPLOY Notibot"
echo "======================================================================"
echo "  Proyecto : $PROJECT_DIR"
echo "  Patch    : $PATCH_DIR"
echo "  Backup   : $BACKUP_DIR"
echo ""

# ── 1. Backup de lo que hay hoy ──────────────────────────────────────────────
mkdir -p "$BACKUP_DIR"
cd "$PATCH_DIR"
FILES=$(find . -type f -not -path "./deploy/*" | sed 's|^\./||' | sort)
cd "$PROJECT_DIR"

BACKED=0
while IFS= read -r f; do
  [ -z "$f" ] && continue
  if [ -f "$f" ]; then
    mkdir -p "$BACKUP_DIR/$(dirname "$f")"
    cp "$f" "$BACKUP_DIR/$f"
    BACKED=$((BACKED + 1))
  fi
done <<< "$FILES"
echo "==> Backup de $BACKED archivos"

# ── 2. Diff de variables.env ─────────────────────────────────────────────────
if [ "$COPY_ENV" -eq 1 ] && [ -f "$PATCH_DIR/variables.env" ] && [ -f variables.env ]; then
  echo ""
  echo "==> Diff variables.env (actual -> nuevo):"
  diff -u variables.env "$PATCH_DIR/variables.env" | sed 's/^/    /' || true
  echo ""
fi

# ── 3. Copiar los archivos nuevos ────────────────────────────────────────────
cd "$PATCH_DIR"
find . -type f -not -path "./deploy/*" -not -name "variables.env" -not -name "APLICAR.sh" | while IFS= read -r f; do
  dest="$PROJECT_DIR/${f#./}"
  mkdir -p "$(dirname "$dest")"
  cp "$f" "$dest"
done
echo "==> Código actualizado"

# deploy/ (scripts)
if [ -d "$PATCH_DIR/deploy" ]; then
  mkdir -p "$PROJECT_DIR/deploy"
  cp -r "$PATCH_DIR/deploy/." "$PROJECT_DIR/deploy/"
  chmod +x "$PROJECT_DIR"/deploy/*.sh 2>/dev/null || true
  echo "==> Scripts de deploy actualizados"
fi

if [ "$COPY_ENV" -eq 1 ] && [ -f "$PATCH_DIR/variables.env" ]; then
  cp "$PATCH_DIR/variables.env" "$PROJECT_DIR/variables.env"
  echo "==> variables.env actualizado"
else
  echo "==> variables.env SIN tocar (--no-env)"
fi

# ── 4. Recrear contenedores ──────────────────────────────────────────────────
# OJO: `docker compose restart` NO recarga el env_file (variables.env).
# Hay que recrear los contenedores para que TOMEN las variables nuevas.
cd "$COMPOSE_DIR"
echo ""
echo "==> Recreando contenedores (para recargar variables.env)..."
SERVICIOS=$(docker compose config --services 2>/dev/null | grep -E "notibot" | tr '\n' ' ')
if [ -z "$SERVICIOS" ]; then
  echo "    AVISO: no pude listar los servicios del compose."
  SERVICIOS="app2-notibot notibot-cron-a notibot-cron-b notibot-cron-c notibot-cron-d"
fi
if ! docker compose up -d --force-recreate $SERVICIOS; then
  echo "    (recreate fallo; pruebo solo restart)"
  docker compose restart $SERVICIOS || {
    echo "    AVISO: no pude reiniciar los contenedores. Reinicialos a mano:"
    echo "    cd $COMPOSE_DIR && docker compose up -d --force-recreate $SERVICIOS"
  }
fi
sleep 6
docker compose ps 2>/dev/null | grep -E "notibot" || true

# ── 5. Smoke test ────────────────────────────────────────────────────────────
echo ""
cd "$PROJECT_DIR"
bash deploy/smoke_test.sh || true

echo ""
echo "======================================================================"
echo " Listo."
echo ""
echo " Probar un cron real (publica 1 artículo y avisa por Telegram):"
echo "   docker exec notibot-cron-a sh -c 'cd /app && python cron_worker_a.py'"
echo ""
echo " Ver el log:"
echo "   docker exec notibot-cron-a sh -c 'tail -60 /app/logs/cron_a.log'"
echo ""
echo " Rollback:"
echo "   cd $PROJECT_DIR && cp -r $BACKUP_DIR/. . && cd $COMPOSE_DIR && docker compose up -d --force-recreate $SERVICIOS"
echo "======================================================================"
