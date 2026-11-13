#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prepare_wp.py — Prepara un WordPress local para el notibot
==========================================================
    python prepare_wp.py /home/mlorenzo/docker/sitio3 --dry-run
    python prepare_wp.py /home/mlorenzo/docker/sitio3
    python prepare_wp.py /home/mlorenzo/docker/sitio3 --instalar-plugin

Hace backup de wp-config.php y .htaccess antes de tocar nada. No duplica la
constante si ya existe. Con --checklist (o si no hay acceso a los archivos)
imprime los pasos manuales para hosting externo.
"""
import argparse
import json
import pathlib
import sys

APP_ROOT = pathlib.Path(__file__).resolve().parent

try:  # consolas Windows en cp1252 rompen al imprimir acentos o símbolos
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
sys.path.insert(0, str(APP_ROOT))

from core import wp_prepare  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepara un WordPress para el notibot")
    parser.add_argument("ruta", nargs="?", help="Carpeta del sitio (o el wp-config.php)")
    parser.add_argument("--dry-run", action="store_true", help="Muestra qué haría, sin escribir")
    parser.add_argument("--instalar-plugin", action="store_true",
                        help="Descarga el plugin JWT del repositorio oficial si falta")
    parser.add_argument("--checklist", action="store_true", help="Solo imprime los pasos manuales")
    parser.add_argument("--json", action="store_true", help="Salida en JSON")
    args = parser.parse_args()

    if args.checklist or not args.ruta:
        print(wp_prepare.checklist())
        return 0

    est = wp_prepare.estado(args.ruta)
    if not est.get("ok"):
        print("✗ %s" % est.get("error"))
        if not est.get("local"):
            print(wp_prepare.checklist())
        return 2

    print("Sitio: %s" % est["ruta"])
    print("wp-config: %s" % est["wp_config"])
    print("plugin JWT: %s | constante: %s | .htaccess: %s" % (
        "sí" if est["plugin"] else "no",
        "sí" if est["constante"] else "no",
        "sí" if est["htaccess"] else "no",
    ))

    resultado = wp_prepare.preparar(args.ruta, dry_run=args.dry_run,
                                    instalar_plugin=args.instalar_plugin)
    if args.json:
        print(json.dumps(resultado, ensure_ascii=False, indent=2))
    else:
        print()
        for paso in resultado["pasos"]:
            print("[%s] %s: %s" % ("ok" if paso["ok"] else "FALLA", paso["accion"], paso["detalle"]))
        print()
        print("Listo." if resultado["ok"] else "Quedaron pasos pendientes (ver arriba).")
    return 0 if resultado["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
