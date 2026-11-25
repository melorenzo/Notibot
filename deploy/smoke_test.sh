#!/usr/bin/env bash
# smoke_test.sh — valida el deploy SIN publicar nada.
#
#   bash /home/mlorenzo/docker/app2-notibot-V2/deploy/smoke_test.sh
#
# Corre dentro del server (usa el contenedor notibot-cron-a).
# No publica artículos, no toca WordPress, no manda nada a Telegram.
#
# OJO: dentro de sh -c '...' no se pueden usar comillas simples (terminan
# el string del shell y el python llega roto). Todo el python va con
# comillas dobles.
set -uo pipefail

CONTAINER="${CONTAINER:-notibot-cron-a}"
FAIL=0

echo "======================================================================"
echo " 1. Imports y lista de fuentes de cada worker"
echo "======================================================================"
docker exec "$CONTAINER" sh -c 'cd /app && python - <<"PY"
import importlib, sys
sys.path.insert(0, "/app")
for w in "abcd":
    m = importlib.import_module("cron_worker_" + w)
    keys = [s["key"] for s in m.SOURCES]
    print("  cron " + w + ": " + str(len(keys)) + " fuentes -> " + str(keys))
import bot_telegram
print("  bot menu:", list(bot_telegram.FUENTES_LABELS.keys()))
PY' || FAIL=1

echo ""
echo "======================================================================"
echo " 2. Scrapers: cuantas noticias trae cada fuente"
echo "======================================================================"
docker exec "$CONTAINER" sh -c 'cd /app && python - <<"PY"
import sys, json, pathlib, subprocess, os
sys.path.insert(0, "/app")
from scraping.rss_sources import rss_sources

SIN_ITEMS = []

def correr(src):
    env = dict(os.environ)
    env.update({k: str(v) for k, v in (src.get("env") or {}).items()})
    env["SCRAPER_OUT_PATH"] = str(src["output"])
    r = subprocess.run([sys.executable, src["script"]], cwd="/app",
                       env=env, capture_output=True, text=True, timeout=180)
    items = []
    p = pathlib.Path(src["output"])
    if p.exists():
        try:
            items = json.loads(p.read_text()) or []
        except Exception:
            items = []
    return r.returncode, items, (r.stdout or "")[-200:]

for src in rss_sources():
    code, items, out = correr(src)
    lab = src["label"]
    ok = "OK   " if items else "FALLA"
    print("  [" + ok + "] " + lab.ljust(16) + str(len(items)).rjust(3) + " items  (exit=" + str(code) + ")")
    if not items:
        print("           " + out.strip()[:180])
        SIN_ITEMS.append(lab)

print()
if SIN_ITEMS:
    print("  Sin items:", ", ".join(SIN_ITEMS))
else:
    print("  Las 3 fuentes RSS traen notas.")
PY' || FAIL=1

echo ""
echo "======================================================================"
echo " 3. Fuentes legacy (una sola, sin publicar)"
echo "======================================================================"
if docker exec "$CONTAINER" sh -c 'cd /app && SCRAPER_OUT_PATH=/tmp/smoke_ba.json python scraping/scraper_ba_static.py'; then
  echo "  [OK   ] ba_static"
else
  echo "  [FALLA] ba_static"
  FAIL=1
fi

AVISOS=""
if docker exec "$CONTAINER" sh -c 'cd /app && SCRAPER_OUT_PATH=/tmp/smoke_linkedin.json python scraping/scraper_linkd.py'; then
  echo "  [OK   ] linkedin"
  docker exec "$CONTAINER" sh -c 'cd /app && python -c "import json;d=json.load(open(\"/tmp/smoke_linkedin.json\")) or [];print(\"         items:\",len(d));[print(\"         -\",(i.get(\"titulo\") or \"\")[:70]) for i in d[:5]]"'
else
  # LinkedIn depende de un sitio externo con muro de sesión: si devuelve 0
  # posts no es un deploy roto, es la fuente legacy que se quedó sin datos.
  echo "  [AVISO] linkedin (0 posts: LinkedIn pide sesión o cambió el DOM; no bloquea el deploy)"
  AVISOS="$AVISOS linkedin"
fi

echo ""
echo "======================================================================"
echo " 4. Variables de entorno effective en el contenedor"
echo "======================================================================"
docker exec "$CONTAINER" sh -c 'cd /app && python - <<"PY"
import os
claves = [
    "APP_ROOT", "CRON_A_DIARIO", "CRON_B_DIARIO", "CRON_C_DIARIO", "CRON_D_DIARIO",
    "BA_STATIC_MAX_PAGES", "SCRAPER_TIMEOUT", "SCRAPER_RETRY_WAIT",
    "SALIDAS_LANACION", "SALIDAS_INFOBAE", "SALIDAS_BA_NOTICIAS",
    "TWITTER_BEARER_TOKEN", "SELENIUM_REMOTE_URL",
    "LINKEDIN_COMPANY", "LINKEDIN_MAX_DAYS", "LINKEDIN_MAX_ITEMS",
]
for k in claves:
    v = os.getenv(k)
    if k == "TWITTER_BEARER_TOKEN":
        v = "(configurado)" if v else "(NO CONFIGURADO -> la fuente X se saltea)"
    print("  " + k.ljust(22) + "= " + str(v))
PY' || true

echo ""
echo "======================================================================"
echo " 5. Estado de archivos (used_items / rotacion)"
echo "======================================================================"
docker exec "$CONTAINER" sh -c 'cd /app && python - <<"PY"
import json, os
for p in ("data/used_items.json", "data/cron_a_rotation.json"):
    if not os.path.exists(p):
        print("  " + p + ": NO EXISTE")
        continue
    try:
        d = json.load(open(p, encoding="utf-8"))
        n = len(d) if isinstance(d, (dict, list)) else "?"
        print("  " + p + ": " + str(n) + " entradas")
    except Exception as e:
        print("  " + p + ": error " + str(e))
PY' || true

echo ""
echo "======================================================================"
if [ "$FAIL" -eq 0 ]; then
  if [ -n "$AVISOS" ]; then
    echo " RESULTADO: OK (avisos:$AVISOS )"
  else
    echo " RESULTADO: todo OK"
  fi
else
  echo " RESULTADO: hay fallas (revisa arriba)"
fi
echo "======================================================================"
exit $FAIL
