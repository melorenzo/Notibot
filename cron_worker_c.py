#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notibot V4 — Cron Worker C
===========================
Diario  : pulsoporteno (Pulso Porteño)
Fuentes : rotación propia + fallback en cascada automático
Horarios: configurar en cron, ej: 0 11,19 * * *  (distintos a A y B)

Crontab ejemplo:
  0 11,19 * * * docker exec notibot_v2 sh -c "cd /app && python cron_worker_c.py >> /app/logs/cron_c.log 2>&1"
"""
import logging
import os
import pathlib
import sys

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app")).resolve()
sys.path.insert(0, str(APP_ROOT))

from cron_base import run_pipeline
from scraping.rss_sources import rss_sources

# ─── Identidad del cron ───────────────────────────────────────────────────────
CRON_ID     = "C"
DIARIO_SLUG = os.getenv("CRON_C_DIARIO", "pulsoporteno")
TONO        = os.getenv("CRON_C_TONO", "entusiasta").strip().lower()
MAX_RETRIES = int(os.getenv("CRON_MAX_RETRIES", "3"))

# ─── Fuentes del Cron C (orden distinto a A y B para variar rotación) ─────────
SOURCES = [
    {
        "key":    "infobae",
        "label":  "Infobae",
        "script": "scraping/scraper_infobae.py",
        "output": APP_ROOT / "salidas_infobae.json",
        "timeout": 90,
        "retries": 2,
    },
    {
        "key":    "equipoba",
        "label":  "Equipo BA",
        "script": "scraping/scraper_equipoba.py",
        "output": APP_ROOT / "salidas_equipoba.json",
        "timeout": 150,
        "retries": 1,
    },
    {
        "key":    "ba_noticias",
        "label":  "BA Noticias",
        "script": "scraping/scraper_ba_noticias.py",
        "output": APP_ROOT / "salidas_ba_noticias.json",
        "timeout": 60,
        "retries": 2,
    },
    {
        "key":    "lanacion",
        "label":  "La Nación Ciudad",
        "script": "scraping/scraper_lanacion.py",
        "output": APP_ROOT / "salidas_lanacion.json",
        "timeout": 120,
        "retries": 2,
    },
    {
        "key":    "ba_static",
        "label":  "BA Noticias (GCBA static)",
        "script": "scraping/scraper_ba_static.py",
        "output": APP_ROOT / "salidas_ba_static.json",
        "timeout": 60,
        "retries": 2,
    },
]

# Fuentes con feed RSS (30 notas nuevas por día cada una)
SOURCES += rss_sources(
    ["infoporteo", "noticiascaba", "caba360"],
    app_root=APP_ROOT,
)

# ─── Archivos de estado ───────────────────────────────────────────────────────
DATA_DIR      = APP_ROOT / "data"
ROTATION_PATH = DATA_DIR / "cron_c_rotation.json"   # rotación independiente
USED_PATH     = DATA_DIR / "used_items.json"          # compartido con A y B → sin duplicados globales

# ─── Logger ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger(f"notibot.cron_{CRON_ID}")


def main():
    run_pipeline(
        cron_id       = CRON_ID,
        diario_slug   = DIARIO_SLUG,
        sources       = SOURCES,
        rotation_path = ROTATION_PATH,
        used_path     = USED_PATH,
        tono          = TONO,
        log           = log,
        max_retries   = MAX_RETRIES,
    )


if __name__ == "__main__":
    main()
