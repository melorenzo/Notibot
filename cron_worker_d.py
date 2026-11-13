#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notibot V4 — Cron Worker D
===========================
Diario  : latin_tinta (La Tinta Digital)
Fuentes : rotación propia + fallback en cascada automático
Horarios: configurar en cron, ej: 0 13,21 * * *  (distintos a A, B y C)

Crontab ejemplo:
  0 13,21 * * * docker exec notibot_v2 sh -c "cd /app && python cron_worker_d.py >> /app/logs/cron_d.log 2>&1"
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
CRON_ID     = "D"
DIARIO_SLUG = os.getenv("CRON_D_DIARIO", "latin_tinta")
TONO        = os.getenv("CRON_D_TONO", "realista").strip().lower()
MAX_RETRIES = int(os.getenv("CRON_MAX_RETRIES", "3"))

# ─── Fuentes del Cron D (orden distinto a A, B y C para variar rotación) ──────
SOURCES = [
    {
        "key":    "lanacion",
        "label":  "La Nación Ciudad",
        "script": "scraping/scraper_lanacion.py",
        "output": APP_ROOT / "salidas_lanacion.json",
        "timeout": 120,
        "retries": 2,
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
        "key":    "ba_static",
        "label":  "BA Noticias (GCBA static)",
        "script": "scraping/scraper_ba_static.py",
        "output": APP_ROOT / "salidas_ba_static.json",
        "timeout": 60,
        "retries": 2,
    },
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
]

# Fuentes con feed RSS (30 notas nuevas por día cada una)
SOURCES += rss_sources(
    ["caba360", "noticiascaba", "infoporteo"],
    app_root=APP_ROOT,
)

# ─── Archivos de estado ───────────────────────────────────────────────────────
DATA_DIR      = APP_ROOT / "data"
ROTATION_PATH = DATA_DIR / "cron_d_rotation.json"   # rotación independiente
USED_PATH     = DATA_DIR / "used_items.json"          # compartido con A, B y C → sin duplicados globales

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
