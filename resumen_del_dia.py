#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resumen_del_dia.py — resumen nocturno de publicaciones por Telegram
==================================================================

Lee los logs de los crons (logs/cron_a.log … cron_h.log), busca las
publicaciones de hoy y manda un solo mensaje al admin con quién publicó y quién
no. Es la segunda confirmación: si un cron muere en silencio (redirección al
log que falla, contenedor caído, sin credenciales), aparece acá.

Uso, dentro de un contenedor del notibot (tiene el token y monta /app):

    docker exec notibot-cron-a python /app/resumen_del_dia.py

Crontab (22:00, después del último cron de las 14:00):

    0 22 * * * docker exec notibot-cron-a python /app/resumen_del_dia.py >> /home/mlorenzo/docker/app2-notibot-V2/logs/resumen.log 2>&1

Variables opcionales:
    RESUMEN_DRY_RUN=1          imprime el mensaje y no lo manda
    RESUMEN_FECHA=YYYY-MM-DD   resume otro día (por defecto, hoy en Buenos Aires)
    RESUMEN_LOGS_DIR=...       otro directorio de logs
    RESUMEN_CHAT_IDS=1,2       destinatarios (por defecto TELEGRAM_USER_ID)
"""

from __future__ import annotations

import html
import json
import os
import pathlib
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from typing import List, Optional, Tuple

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app")).resolve()

# Los mismos slugs por defecto que cada cron_worker_X.py, para que el resumen
# funcione aunque falte la variable de entorno.
SLUGS_POR_DEFECTO = {
    "A": "palabras_red",
    "B": "horizonteba",
    "C": "pulsoporteno",
    "D": "latin_tinta",
    "E": "diario_ciudad",
    "F": "egos_noticias",
    "G": "loscienbarrios",
    "H": "delidiet",
}

RE_FECHA_HORA = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2}")
RE_PUBLICADO = re.compile(r"✅\s*Publicado(?:\s+ID\s+\d+)?\s*:\s*(\S+)")


# ─── datos ────────────────────────────────────────────────────────────────────
def fecha_objetivo() -> str:
    fija = (os.getenv("RESUMEN_FECHA") or "").strip()
    if fija:
        return fija
    try:
        from zoneinfo import ZoneInfo
        ahora = datetime.now(ZoneInfo("America/Argentina/Buenos_Aires"))
    except Exception:
        ahora = datetime.now()
    return ahora.strftime("%Y-%m-%d")


def nombre_diario(slug: str) -> str:
    try:
        diarios = json.loads(
            (APP_ROOT / "config" / "diarios.json").read_text(encoding="utf-8")
        )
        nombre = (diarios.get(slug) or {}).get("name") or ""
        if nombre:
            return nombre
    except Exception:
        pass
    return slug


def logs_dir() -> pathlib.Path:
    override = (os.getenv("RESUMEN_LOGS_DIR") or "").strip()
    return pathlib.Path(override) if override else APP_ROOT / "logs"


def publicaciones_del_dia(texto: str, fecha: str) -> List[Tuple[str, str]]:
    """[(hora, url)] de las publicaciones con esa fecha."""
    salida: List[Tuple[str, str]] = []
    for linea in texto.splitlines():
        m_fecha = RE_FECHA_HORA.match(linea)
        if not m_fecha or m_fecha.group(1) != fecha:
            continue
        m_pub = RE_PUBLICADO.search(linea)
        if m_pub:
            salida.append((m_fecha.group(2), m_pub.group(1)))
    return salida


def corrio_ese_dia(texto: str, fecha: str) -> bool:
    for linea in texto.splitlines():
        m = RE_FECHA_HORA.match(linea)
        if m and m.group(1) == fecha:
            return True
    return False


# ─── mensaje ──────────────────────────────────────────────────────────────────
def armar_mensaje(fecha: str) -> str:
    ok: List[str] = []
    fallas: List[str] = []
    total = 0

    for letra in sorted(SLUGS_POR_DEFECTO):
        slug = (os.getenv(f"CRON_{letra}_DIARIO") or SLUGS_POR_DEFECTO[letra]).strip()
        if not slug:
            continue
        total += 1
        nombre = html.escape(nombre_diario(slug))
        archivo = logs_dir() / f"cron_{letra.lower()}.log"
        try:
            texto = archivo.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            texto = ""

        pubs = publicaciones_del_dia(texto, fecha)
        if pubs:
            hora, url = pubs[-1]
            ok.append(f"✅ <b>{nombre}</b> · Cron {letra} · {hora}\n{url}")
        elif corrio_ese_dia(texto, fecha):
            fallas.append(
                f"❌ <b>{nombre}</b> · Cron {letra} — corrió pero no publicó "
                f"(ver logs/cron_{letra.lower()}.log)"
            )
        else:
            fallas.append(
                f"❌ <b>{nombre}</b> · Cron {letra} — no corrió hoy "
                f"(logs/cron_{letra.lower()}.log sin líneas de la fecha)"
            )

    dia = fecha[8:10] + "/" + fecha[5:7] + "/" + fecha[0:4]
    cabecera = [
        f"📊 <b>Resumen del día — {dia}</b>",
        "",
        f"✅ Publicaron: {len(ok)} de {total}",
    ]
    if fallas:
        cabecera.append(f"❌ Faltaron: {len(fallas)}")
    cabecera.append("")

    partes = cabecera + ok + ([""] if ok and fallas else []) + fallas
    partes += ["", "<i>Notibot V4 · crons del host</i>"]
    return "\n".join(partes)


# ─── envío ────────────────────────────────────────────────────────────────────
def enviar(texto: str, destinos: List[str], token: str) -> bool:
    todos_ok = True
    for cid in destinos:
        datos = urllib.parse.urlencode({
            "chat_id": cid,
            "text": texto,
            "parse_mode": "HTML",
            "disable_web_page_preview": "true",
        }).encode()
        try:
            with urllib.request.urlopen(
                f"https://api.telegram.org/bot{token}/sendMessage", datos, timeout=20
            ) as r:
                cuerpo = r.read().decode("utf-8", "replace")
                if r.status != 200:
                    todos_ok = False
                    print(f"Telegram chat {cid}: HTTP {r.status} {cuerpo[:160]}")
        except Exception as e:  # nunca romper el resumen por un destinatario
            todos_ok = False
            print(f"Telegram chat {cid}: {e}")
    return todos_ok


def main() -> int:
    # El resumen lleva emojis: en un locale que no sea UTF-8 (consola de Windows,
    # un LANG raro) un print no debe tirar el script abajo.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    fecha = fecha_objetivo()
    mensaje = armar_mensaje(fecha)

    if (os.getenv("RESUMEN_DRY_RUN") or "").strip() in {"1", "true", "si", "sí"}:
        print(mensaje)
        return 0

    token = (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN") or "").strip()
    destinos = [
        c.strip() for c in (os.getenv("RESUMEN_CHAT_IDS") or "").split(",") if c.strip()
    ] or [(os.getenv("TELEGRAM_USER_ID") or "").strip()]
    destinos = [d for d in destinos if d]

    if not token or not destinos:
        print("Telegram no configurado (falta TELEGRAM_BOT_TOKEN o TELEGRAM_USER_ID).")
        return 1

    print(mensaje)
    return 0 if enviar(mensaje, destinos, token) else 1


if __name__ == "__main__":
    sys.exit(main())
