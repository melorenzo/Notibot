#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron_worker.py — Worker genérico del Notibot
============================================
Corre el pipeline para cualquier diario de `config/diarios.json`, sin editar
archivos ni agregar un script nuevo por diario.

    python cron_worker.py <slug> [--notas N] [--ahora]

- `--notas N`  publica hasta N notas en la misma corrida (default 1).
- `--ahora`    ignora el campo `activo` (para una corrida manual puntual).

Estado por diario:
    data/<slug>_rotation.json   rotación de fuentes
    data/used_items.json        historial compartido de deduplicación
    logs/<slug>.log             log de la corrida

Configuración opcional en `config/panel_state.json` (la escribe el panel):
    tonos, horarios, orden_fuentes, gestionado_por, notas_por_corrida
"""
import argparse
import json
import logging
import logging.handlers
import os
import pathlib
import random
import sys

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT") or pathlib.Path(__file__).resolve().parent).resolve()
sys.path.insert(0, str(APP_ROOT))

from cron_base import run_pipeline                      # noqa: E402
from core.diarios import all_diarios                    # noqa: E402
from scraping.rss_sources import rss_sources            # noqa: E402

DATA_DIR = APP_ROOT / "data"
LOGS_DIR = APP_ROOT / "logs"
STATE_PATH = APP_ROOT / "config" / "panel_state.json"

TONOS_VALIDOS = ["serio", "entusiasta", "realista", "ironico"]

# Mismas fuentes que los crons A..H. La rotación elige cuál va primero.
SOURCES_BASE = [
    {
        "key": "ba_static",
        "label": "BA Noticias (GCBA static)",
        "script": "scraping/scraper_ba_static.py",
        "output": APP_ROOT / "salidas_ba_static.json",
        "timeout": 60,
        "retries": 2,
    },
    {
        "key": "ba_noticias",
        "label": "BA Noticias",
        "script": "scraping/scraper_ba_noticias.py",
        "output": APP_ROOT / "salidas_ba_noticias.json",
        "timeout": 60,
        "retries": 2,
    },
    {
        "key": "lanacion",
        "label": "La Nación Ciudad",
        "script": "scraping/scraper_lanacion.py",
        "output": APP_ROOT / "salidas_lanacion.json",
        "timeout": 120,
        "retries": 2,
    },
    {
        "key": "infobae",
        "label": "Infobae",
        "script": "scraping/scraper_infobae.py",
        "output": APP_ROOT / "salidas_infobae.json",
        "timeout": 90,
        "retries": 2,
    },
    {
        "key": "equipoba",
        "label": "Equipo BA",
        "script": "scraping/scraper_equipoba.py",
        "output": APP_ROOT / "salidas_equipoba.json",
        "timeout": 150,
        "retries": 1,
    },
    {
        "key": "x",
        "label": "X (Twitter)",
        "script": "scraping/scraper_x.py",
        "output": APP_ROOT / "salidas_x.json",
        "timeout": 60,
        "retries": 1,
        "requires_env": ["TWITTER_BEARER_TOKEN"],
    },
    {
        "key": "linkedin",
        "label": "LinkedIn",
        "script": "scraping/scraper_linkd.py",
        "output": APP_ROOT / "salidas_linkedin.json",
        "timeout": 150,
        "retries": 1,
    },
]

ORDEN_ESTANDAR = [
    "caba360", "noticiascaba", "infoporteo",
    "ba_static", "lanacion", "ba_noticias", "infobae", "equipoba", "linkedin", "x",
]


def catalogo_fuentes():
    fuentes = list(SOURCES_BASE) + rss_sources(["caba360", "noticiascaba", "infoporteo"], app_root=APP_ROOT)
    por_key = {f["key"]: f for f in fuentes}
    return por_key


def leer_estado_panel():
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8")).get("diarios") or {}
    except Exception:
        return {}


def main():
    parser = argparse.ArgumentParser(description="Notibot — worker genérico por diario")
    parser.add_argument("slug")
    parser.add_argument("--notas", type=int, default=1)
    parser.add_argument("--ahora", action="store_true")
    args = parser.parse_args()

    slug = args.slug.strip()
    diarios = all_diarios()
    cfg = diarios.get(slug)
    if not cfg:
        print("Diario '%s' no existe en config/diarios.json" % slug, file=sys.stderr)
        return 2
    if not cfg.get("activo", True) and not args.ahora:
        print("Diario '%s' está pausado. Usá --ahora para forzar." % slug)
        return 0

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log = logging.getLogger("notibot.%s" % slug)
    log.setLevel(logging.INFO)
    formato = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    handler_archivo = logging.handlers.RotatingFileHandler(
        str(LOGS_DIR / ("%s.log" % slug)), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler_archivo.setFormatter(formato)
    handler_consola = logging.StreamHandler(sys.stdout)
    handler_consola.setFormatter(formato)
    log.addHandler(handler_archivo)
    log.addHandler(handler_consola)

    estado = leer_estado_panel().get(slug) or {}
    tonos = [t for t in (estado.get("tonos") or []) if t in TONOS_VALIDOS] or ["realista"]

    por_key = catalogo_fuentes()
    preferidas = [k for k in (estado.get("orden_fuentes") or []) if k in por_key]
    resto = [k for k in ORDEN_ESTANDAR if k in por_key and k not in preferidas]
    sobrantes = [k for k in por_key if k not in preferidas and k not in resto]
    sources = [por_key[k] for k in preferidas + resto + sobrantes]

    notas = max(1, min(int(args.notas or 1), 5))
    log.info("Worker %s — %d nota(s) — fuentes: %s", slug, notas, ", ".join(s["key"] for s in sources[:3]))

    rotation_path = DATA_DIR / ("%s_rotation.json" % slug)
    used_path = pathlib.Path(cfg.get("used_path") or (DATA_DIR / "used_items.json"))
    max_retries = int(os.getenv("CRON_MAX_RETRIES", "3"))

    for i in range(notas):
        tono = random.choice(tonos)
        log.info("--- Corrida %d/%d (tono %s) ---", i + 1, notas, tono)
        run_pipeline(
            cron_id=slug,
            diario_slug=slug,
            sources=sources,
            rotation_path=rotation_path,
            used_path=used_path,
            tono=tono,
            log=log,
            max_retries=max_retries,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
