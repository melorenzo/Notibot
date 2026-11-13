#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notibot V4 — Cron Worker F
===========================
Diario  : egos_noticias (Egos Argentinos)
Fuentes : mismas que A/B/C, con orden propio de prioridad
Horarios: 0 13 * * *  (una pasada por día, 13hs)

Crontab ejemplo:
  0 13 * * * docker exec notibot-cron-f sh -c "cd /app && python cron_worker_f.py >> /app/logs/cron_f.log 2>&1"
"""
import json
import logging
import os
import pathlib
import sys

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app")).resolve()
sys.path.insert(0, str(APP_ROOT))

import cron_base
from cron_base import run_pipeline, TONO_MAP
from scraping.rss_sources import rss_sources

# ─── Identidad del cron ───────────────────────────────────────────────────────
CRON_ID     = "F"
DIARIO_SLUG = os.getenv("CRON_F_DIARIO", "egos_noticias")
# Tonos que rotan en cada corrida (válidos: serio, entusiasta, realista, ironico). Se pisa con CRON_F_TONOS="a,b,c"
TONOS = [t.strip().lower() for t in os.getenv("CRON_F_TONOS", "ironico,entusiasta").split(",")
         if t.strip().lower() in TONO_MAP] or ["realista"]
MAX_RETRIES = int(os.getenv("CRON_MAX_RETRIES", "3"))
# IDs de Telegram extra que reciben el aviso de "Publicado" (separados por coma)
NOTIFY_IDS = [x.strip() for x in os.getenv("CRON_F_NOTIFY", os.getenv("NOTIFY_EXTRA_IDS", "")).split(",") if x.strip()]

# ─── Catálogo de fuentes (igual para todos los workers) ───────────────────────
_BASE = [
    {"key": "infobae",     "label": "Infobae",                   "script": "scraping/scraper_infobae.py",
     "output": APP_ROOT / "salidas_infobae.json",     "timeout": 90,  "retries": 2},
    {"key": "equipoba",    "label": "Equipo BA",                 "script": "scraping/scraper_equipoba.py",
     "output": APP_ROOT / "salidas_equipoba.json",    "timeout": 150, "retries": 1},
    {"key": "ba_noticias", "label": "BA Noticias",               "script": "scraping/scraper_ba_noticias.py",
     "output": APP_ROOT / "salidas_ba_noticias.json", "timeout": 60,  "retries": 2},
    {"key": "lanacion",    "label": "La Nación Ciudad",          "script": "scraping/scraper_lanacion.py",
     "output": APP_ROOT / "salidas_lanacion.json",    "timeout": 120, "retries": 2},
    {"key": "ba_static",   "label": "BA Noticias (GCBA static)", "script": "scraping/scraper_ba_static.py",
     "output": APP_ROOT / "salidas_ba_static.json",   "timeout": 60,  "retries": 2},
]
_BASE += rss_sources(["infoporteo", "noticiascaba", "caba360"], app_root=APP_ROOT)

# ─── Orden de prioridad propio de este worker ─────────────────────────────────
ORDER = ['lanacion', 'noticiascaba', 'infobae', 'infoporteo', 'equipoba', 'caba360', 'ba_static', 'ba_noticias']
_pos = {k: i for i, k in enumerate(ORDER)}
SOURCES = sorted(_BASE, key=lambda s: _pos.get(s["key"], len(ORDER)))

# ─── Archivos de estado ───────────────────────────────────────────────────────
DATA_DIR      = APP_ROOT / "data"
ROTATION_PATH = DATA_DIR / "cron_f_rotation.json"   # rotación independiente
TONO_PATH     = DATA_DIR / "cron_f_tono.json"       # estado de la rotación de tono
USED_PATH     = DATA_DIR / "used_items.json"          # compartido → sin duplicados globales

# ─── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(f"notibot.cron_{CRON_ID}")


def next_tono() -> str:
    """Devuelve el tono que toca y avanza el índice (se guarda antes de usarlo)."""
    try:
        idx = int(json.loads(TONO_PATH.read_text(encoding="utf-8")).get("index", -1))
    except Exception:
        idx = -1
    idx = (idx + 1) % len(TONOS)
    try:
        TONO_PATH.parent.mkdir(parents=True, exist_ok=True)
        TONO_PATH.write_text(json.dumps({"index": idx}), encoding="utf-8")
    except Exception as e:
        log.warning("No pude guardar estado de tono: %s", e)
    return TONOS[idx]


def main():
    cron_base.NOTIFY_EXTRA_IDS = NOTIFY_IDS
    run_pipeline(
        cron_id       = CRON_ID,
        diario_slug   = DIARIO_SLUG,
        sources       = SOURCES,
        rotation_path = ROTATION_PATH,
        used_path     = USED_PATH,
        tono          = next_tono(),
        log           = log,
        max_retries   = MAX_RETRIES,
    )


if __name__ == "__main__":
    main()
