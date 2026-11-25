#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notibot (Telegram) - versión integrada con:
- core.diarios (config de diarios)
- core.token_store (tokens JWT automáticos, renovables)
- scrapers externos (La Nación, Infobae, LinkedIn, X, Comuna 15)
- Gemini para refritar noticias
"""

from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import pathlib
import re
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

# --- Integración con tu stack de JWT/diarios ---
from core.diarios import posts_url, media_url, all_diarios
from core.token_store import get_token_for_diario

# ---------------------------
# Logging
# ---------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("notibot")

# ---------------------------
# Paths base
# ---------------------------
APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app")).resolve()
CONFIG_DIR = APP_ROOT / "config"
DATA_DIR = APP_ROOT / "data"
UP_DIR = APP_ROOT / "uploading" / "imagenes_subir"
DEBUG_DIR = APP_ROOT / "debug"

for p in (CONFIG_DIR, DATA_DIR, UP_DIR, DEBUG_DIR):
    p.mkdir(parents=True, exist_ok=True)

USED_ITEMS_LOG = DATA_DIR / "used_items.json"

SALIDAS = {
    "lanacion": APP_ROOT / "salidas_lanacion.json",
    "infobae": APP_ROOT / "salidas_infobae.json",
    "linkedin": APP_ROOT / "salidas_linkedin.json",
    "x": APP_ROOT / "salidas_x.json",
    "ba": APP_ROOT / "salidas_ba_noticias.json",
    "ba_static": APP_ROOT / "salidas_ba_static.json",
    "caba360": APP_ROOT / "salidas_caba360.json",
    "infoporteo": APP_ROOT / "salidas_infoporteo.json",
    "noticiascaba": APP_ROOT / "salidas_noticiascaba.json",
    }

# ---------------------------
# Carga de diarios (vía core.diarios)
# ---------------------------
def load_diarios() -> Dict[str, Dict[str, Any]]:
    try:
        return all_diarios()
    except Exception as e:
        log.error("No pude cargar config de diarios: %s", e)
        return {}

DIARIOS: Dict[str, Dict[str, Any]] = load_diarios()


def _mtime_diarios() -> Optional[int]:
    try:
        return (CONFIG_DIR / "diarios.json").stat().st_mtime_ns
    except Exception:
        return None


_DIARIOS_MTIME: Optional[int] = _mtime_diarios()


def recargar_diarios(forzar: bool = False) -> int:
    """
    Recarga config/diarios.json si cambió en disco.

    El panel da de alta diarios escribiendo ese archivo; así el bot los ve sin
    reiniciar el container. Devuelve la cantidad de diarios cargados.
    """
    global DIARIOS, _DIARIOS_MTIME
    actual = _mtime_diarios()
    if not forzar and actual is not None and actual == _DIARIOS_MTIME:
        return len(DIARIOS)
    nuevos = load_diarios()
    if nuevos:
        DIARIOS = nuevos
        _DIARIOS_MTIME = actual
    return len(DIARIOS)


def diarios_habilitados_para(uid: int) -> Dict[str, Dict[str, Any]]:
    """
    Filtra diarios según `allowed_telegram_ids` (o `allowed_telegram` viejos).
    Si no hay nada configurado, se considera habilitado para todos.
    """
    recargar_diarios()
    result: Dict[str, Dict[str, Any]] = {}
    for slug, cfg in DIARIOS.items():
        allowed_map = cfg.get("allowed_telegram_ids") or cfg.get("allowed_telegram")
        if not allowed_map:
            # sin restricciones
            result[slug] = cfg
            continue

        ids: List[int] = []
        if isinstance(allowed_map, dict):
            ids = [int(k) for k in allowed_map.keys()]
        elif isinstance(allowed_map, list):
            ids = [int(x) for x in allowed_map]

        if uid in ids:
            result[slug] = cfg

    return result


# ---------------------------
# Alias amigable (usa alias de diarios si existen)
# ---------------------------
GLOBAL_ALIASES: Optional[Dict[str, str]] = None

def _load_global_aliases() -> Dict[str, str]:
    global GLOBAL_ALIASES
    if GLOBAL_ALIASES is not None:
        return GLOBAL_ALIASES
    path = CONFIG_DIR / "aliases.json"
    try:
        GLOBAL_ALIASES = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        GLOBAL_ALIASES = {}
    return GLOBAL_ALIASES

def _alias_for(uid: int, diario_slug: Optional[str], update: Optional[Update]) -> str:
    # 1) Primero intento con alias propios del diario (allowed_telegram_ids)
    recargar_diarios()
    if diario_slug and diario_slug in DIARIOS:
        cfg = DIARIOS[diario_slug]
        allowed_map = cfg.get("allowed_telegram_ids") or {}
        alias = allowed_map.get(str(uid))
        if alias:
            return alias

    # 2) Luego aliases globales
    aliases = _load_global_aliases()
    key = f"{uid}:{diario_slug}" if diario_slug else str(uid)
    if key in aliases:
        return aliases[key]

    if update and update.effective_user:
        return (
            update.effective_user.full_name
            or update.effective_user.username
            or f"usuario {uid}"
        )
    return f"usuario {uid}"


# ---------------------------
# WordPress con JWT auto-refresh
# ---------------------------
def wp_upload_media(diario_cfg: Dict[str, Any], file_path: pathlib.Path) -> Optional[int]:
    """
    Sube una imagen usando el token auto-gestionado por token_store.
    """
    if not file_path.exists():
        log.warning("No existe el archivo de imagen: %s", file_path)
        return None

    token = get_token_for_diario(diario_cfg)  # acá se renueva si está caído
    headers = {"Authorization": f"Bearer {token}"}
    mime, _ = mimetypes.guess_type(str(file_path))
    mime = mime or "image/jpeg"

    files = {"file": (file_path.name, file_path.open("rb"), mime)}

    url = media_url(diario_cfg)
    log.info("Subiendo media a %s", url)
    resp = requests.post(url, headers=headers, files=files, timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Error subiendo imagen: {resp.status_code} {resp.text}")
    data = resp.json()
    return data.get("id")


def wp_update_post_image(diario_cfg: Dict[str, Any], post_id: int, media_id: int) -> bool:
    """
    Actualiza el featured_media de un post ya publicado.
    Usa POST /<id> que es compatible con JWT (equivale a PATCH).
    """
    token = get_token_for_diario(diario_cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    base = posts_url(diario_cfg)  # ej: https://sitio.com/wp-json/wp/v2/posts
    url = f"{base}/{post_id}"
    log.info("Actualizando featured_media del post %s en %s", post_id, url)
    resp = requests.post(
        url,
        headers=headers,
        data=json.dumps({"featured_media": media_id}),
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        log.error("Error actualizando post: %s %s", resp.status_code, resp.text[:200])
        return False
    return True

def wp_create_post(
    diario_cfg: Dict[str, Any],
    title: str,
    content: str,
    featured_media: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Crea un post en WordPress con:
    - token renovable (get_token_for_diario)
    - default_status, default_categories, default_tags, default_author
    Devuelve el JSON completo de la respuesta de WP.
    """
    token = get_token_for_diario(diario_cfg)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    payload: Dict[str, Any] = {
        "title": title,
        "content": content,
        "status": diario_cfg.get("default_status", "publish"),
    }

    cats = diario_cfg.get("default_categories") or []
    if cats:
        payload["categories"] = cats

    tags = diario_cfg.get("default_tags") or []
    if tags:
        payload["tags"] = tags

    author = diario_cfg.get("default_author")
    if author:
        payload["author"] = author

    if featured_media:
        payload["featured_media"] = featured_media

    url = posts_url(diario_cfg)
    log.info("Creando post en %s", url)
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Error creando post: {resp.status_code} {resp.text}")
    return resp.json()


# ---------------------------
# Utilidades varias
# ---------------------------

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto = (
        "🤖 *Notibot V2\\.1*\n\n"
        "- Scrapea noticias de La Nación, Infobae, LinkedIn, X y ahora también de la *Comuna 15*\\.\n"
        "- Refrita las notas con IA \\(Gemini\\) para que el contenido sea original\\.\n"
        "- Te deja editar título, cuerpo e imagen antes de publicar\\.\n"
        "- Publica en tu WordPress usando JWT automático por diario\\.\n\n"
        "Comandos disponibles:\n"
        "• /start  → Iniciar flujo de publicación\n"
        "• /agregar → Modo manual \\(cargar título/cuerpo propios\\)\n"
        "• /help   → Ver esta ayuda\n"
    )
    await update.message.reply_text(texto, parse_mode=ParseMode.MARKDOWN_V2)


async def recargar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recarga la lista de diarios (útil después de un alta desde el panel)."""
    uid = str(update.effective_user.id) if update.effective_user else ""
    admin = (os.getenv("TELEGRAM_USER_ID") or "").strip()
    if admin and uid != admin:
        await update.message.reply_text("Este comando es solo para el admin.")
        return
    total = recargar_diarios(forzar=True)
    await update.message.reply_text("✅ Listo: %d diarios cargados." % total)


def _load_used_items() -> Dict[str, Dict[str, Any]]:
    """
    Carga used_items.json y lo normaliza SIEMPRE a dict.

    Soporta:
    - {} (dict moderno)
    - [] (lista vieja)  -> se convierte a dict usando 'hash' si existe
    """
    if not USED_ITEMS_LOG.exists():
        return {}
    try:
        data = json.loads(USED_ITEMS_LOG.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if isinstance(data, dict):
        return data

    if isinstance(data, list):
        new: Dict[str, Dict[str, Any]] = {}
        for idx, entry in enumerate(data):
            if isinstance(entry, dict):
                h = entry.get("hash")
                key = h if isinstance(h, str) else str(idx)
                new[key] = entry
        return new

    return {}

def _save_used_items(data: Dict[str, Dict[str, Any]]):
    try:
        USED_ITEMS_LOG.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception as e:
        log.error("No se pudo guardar used_items.json: %s", e)

def _hash_title_description(title: str, description: str) -> str:
    from core.dedup import hash_item

    return hash_item(title, description)

def _clean_title(t: str) -> str:
    t = (t or "").strip()
    t = re.sub(r"^\**\s*(t[ií]tulo[:\-]\s*)", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\*+", "", t)
    return t.strip()

def _clean_description(d: str) -> str:
    d = (d or "").strip()
    d = d.replace("\r", "")
    d = re.sub(r"^\s*descripci[óo]n[:\-]\s*", "", d, flags=re.IGNORECASE)
    return d.strip()


def _item_ya_usada(item: Dict[str, Any], used: Dict[str, Any]) -> bool:
    """Mismo criterio de duplicados que los crons (URL + título + legacy)."""
    try:
        from core.dedup import is_used

        limpio = dict(item)
        limpio["titulo"] = _clean_title(item.get("titulo") or "")
        limpio["descripcion"] = _clean_description(item.get("descripcion") or "")
        return is_used(limpio, used)
    except Exception:
        h = _hash_title_description(
            _clean_title(item.get("titulo") or ""),
            _clean_description(item.get("descripcion") or ""),
        )
        return h in used


def _hashes_extra(item: Dict[str, Any]) -> List[str]:
    """Hashes por URL y por título normalizado (dedup con los crons)."""
    try:
        from core.dedup import item_hashes

        limpio = dict(item)
        limpio["titulo"] = _clean_title(item.get("titulo") or "")
        limpio["descripcion"] = _clean_description(item.get("descripcion") or "")
        return list(item_hashes(limpio)[1])
    except Exception:
        return []

def _es_imagen_aceptable(file_path: pathlib.Path) -> bool:
    mime, _ = mimetypes.guess_type(str(file_path))
    return bool(mime and mime.startswith("image/"))

def _shorten(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: n - 3].rstrip() + "..."

# NUEVA FUNCIÓN: Escapar caracteres especiales para MarkdownV2
def _escape_markdown_v2(text: str) -> str:
    """
    Escapa los caracteres especiales de MarkdownV2 de Telegram.
    Caracteres a escapar: _ * [ ] ( ) ~ ` > # + - = | { } . !
    """
    if not text:
        return ""
    # Caracteres que necesitan escape en MarkdownV2
    chars_to_escape = r'_*[]()~`>#+-=|{}.!'
    escaped = text
    for char in chars_to_escape:
        escaped = escaped.replace(char, f'\\{char}')
    return escaped

# ---------------------------
# Estados de la conversación
# ---------------------------
(
    ELEGIR_DIARIO,
    ELEGIR_TONO,
    ELEGIR_FUENTE,
    ELEGIR_ITEM,
    REUSO_DECISION,
    PREVIEW_EDICION,
    ESPERAR_TITULO,
    ESPERAR_CUERPO,
    ESPERAR_IMAGEN,
    CONFIRMAR_PUBLICACION,
    CAMBIAR_IMAGEN_CRON,   # flujo de cambio de imagen desde notificación del cron
) = range(11)

MANUAL_LABEL = "➕ Agregar noticia a mano"

TONOS_LABELS = {
    "entusiasta": "🔥 Entusiasta",
    "serio": "📰 Serio",
    "realista": "🧩 Realista",
}
TONOS_BY_NAME = {v.lower(): k for k, v in TONOS_LABELS.items()}
DEFAULT_TONO = os.getenv("NOTIBOT_TONO_DEFAULT", "serio").strip().lower()

# ---------------------------
# Gemini
# ---------------------------
try:
    from procesing.parser_ai import parse_ai_output
except ImportError:
    try:
        from processing.parser_ai import parse_ai_output
    except ImportError:
        from parser_ai import parse_ai_output

def _parse_gemini_block(s: str) -> Tuple[str, str]:
    """
    Espera algo tipo:
      Título: ...
      Cuerpo: ...
    pero es tolerante.
    """
    if not s:
        return "", ""
    s = s.strip()
    m = re.search(r"T[ií]tulo\s*:\s*(.+)", s, flags=re.IGNORECASE)
    if m:
        title = m.group(1).strip()
        m2 = re.search(r"Cuerpo\s*:\s*(.+)$", s, flags=re.IGNORECASE | re.DOTALL)
        if m2:
            body = m2.group(1).strip()
        else:
            lines = s.splitlines()
            body = "\n".join(lines[1:]).strip()
        return title, body

    lines = s.splitlines()
    if not lines:
        return "", ""
    if len(lines) == 1:
        return lines[0].strip(), ""
    return lines[0].strip(), "\n".join(lines[1:]).strip()

def _procesar_con_gemini(texto: str, tono: str = "serio") -> str:
    """
    Llama a tu módulo existente de Gemini (procesar_con_gemini),
    respetando tu implementación actual.
    """
    try:
        try:
            from processing.procesar_gemini import procesar_con_gemini
        except ImportError:
            try:
                from procesing.procesar_gemini import procesar_con_gemini
            except ImportError:
                from procesar_gemini import procesar_con_gemini

        return procesar_con_gemini(texto, tono=tono)
    except Exception:
        log.error("Error en procesar_con_gemini:\n%s", traceback.format_exc())
        return texto


# ---------------------------
# Scrapers
# ---------------------------
SCRAPER_CMDS = {
    "lanacion": ["python", "scraping/scraper_lanacion.py"],
    "infobae": ["python", "scraping/scraper_infobae.py"],
    "linkedin": ["python", "scraping/scraper_linkd.py"],
    "x": ["python", "scraping/scraper_x.py"],
    "ba": ["python", "scraping/scraper_ba_noticias.py"],
    "ba_static": ["python", "scraping/scraper_ba_static.py"],
    "caba360": ["python", "scraping/scraper_rss.py"],
    "infoporteo": ["python", "scraping/scraper_rss.py"],
    "noticiascaba": ["python", "scraping/scraper_rss.py"],}

FUENTES_LABELS = {
    "lanacion": "La Nación",
    "infobae": "Infobae",
    "linkedin": "LinkedIn",
    "x": "X",
    "ba": "Noticias BA (GCBA)",
    "ba_static": "BA Static (GCBA)",
    "caba360": "CABA 360",
    "infoporteo": "Infoporteño",
    "noticiascaba": "Noticias CABA",}

FUENTES_BY_NAME = {v.lower(): k for k, v in FUENTES_LABELS.items()}

# Fuentes con feed RSS: comparten script y se configuran por variables de entorno
RSS_FUENTES = ("caba360", "infoporteo", "noticiascaba")

try:
    from scraping.rss_sources import rss_source as _rss_source
except Exception:
    _rss_source = None


def _scraper_env(fuente_key: str) -> dict:
    env = dict(os.environ)
    out = SALIDAS.get(fuente_key)
    if out:
        env["SCRAPER_OUT_PATH"] = str(out)
    if fuente_key in RSS_FUENTES and _rss_source:
        try:
            env.update({k: str(v) for k, v in (_rss_source(fuente_key).get("env") or {}).items()})
        except Exception as e:
            log.warning("No pude config RSS %s: %s", fuente_key, e)
    return env


async def _run_scraper(fuente_key: str) -> bool:
    cmd = SCRAPER_CMDS.get(fuente_key)
    if not cmd:
        log.warning("No hay comando scraper para %s", fuente_key)
        return False
    try:
        # Se borra la salida vieja: si el scraper falla no queremos mostrar
        # noticias de la corrida anterior como si fueran nuevas.
        out = SALIDAS.get(fuente_key)
        if out and out.exists():
            out.unlink()

        log.info("Ejecutando scraper %s: %r", fuente_key, cmd)
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(APP_ROOT), env=_scraper_env(fuente_key)
        )
        stdout, stderr = await proc.communicate()
        ok = proc.returncode == 0
        if not ok:
            log.warning(
                "Scraper %s fallo (exit=%s) stdout=%s stderr=%s",
                fuente_key, proc.returncode,
                (stdout or b"").decode("utf-8", "replace")[-400:],
                (stderr or b"").decode("utf-8", "replace")[-300:],
            )
        log.info("Scraper %s finalizado ok=%s", fuente_key, ok)
        return ok
    except Exception as e:
        log.error("Error corriendo scraper %s: %s", fuente_key, e)
        return False


# ---------------------------
# /start
# ---------------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = int(update.effective_user.id)
    mis_diarios = diarios_habilitados_para(uid)
    if not mis_diarios:
        await update.message.reply_text(
            "No tenés diarios habilitados. Hablá con Maty para que te habilite en config/diarios.json."
        )
        return ConversationHandler.END

    rows: List[List[KeyboardButton]] = []
    for slug, info in mis_diarios.items():
        name = info.get("name") or slug
        rows.append([KeyboardButton(f"{slug} — {name}")])

    await update.message.reply_text(
        "Hola! Elegí el diario con el que querés trabajar:",
        reply_markup=ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True),
    )
    return ELEGIR_DIARIO


# ---------------------------
# Elegir diario
# ---------------------------
async def elegir_diario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = int(update.effective_user.id)
    mis_diarios = diarios_habilitados_para(uid)
    txt = (update.message.text or "").strip()

    chosen_slug: Optional[str] = None

    # 1) match "slug — name"
    for slug, info in mis_diarios.items():
        name = info.get("name") or slug
        if txt == f"{slug} — {name}":
            chosen_slug = slug
            break

    # 2) slug puro
    if chosen_slug is None:
        for slug in mis_diarios.keys():
            if txt.lower() == slug.lower():
                chosen_slug = slug
                break

    # 3) nombre del diario
    if chosen_slug is None:
        for slug, info in mis_diarios.items():
            if (info.get("name") or "").strip().lower() == txt.lower():
                chosen_slug = slug
                break

    if not chosen_slug:
        await update.message.reply_text("No reconocí ese diario. Probá tocar un botón.")
        return ELEGIR_DIARIO

    cfg = mis_diarios[chosen_slug]
    context.user_data.clear()
    context.user_data["diario_slug"] = chosen_slug
    context.user_data["diario_cfg"] = cfg

    alias = _alias_for(uid, chosen_slug, update)

    botones = [[KeyboardButton(TONOS_LABELS[k])] for k in ("entusiasta", "serio", "realista")]

    await update.message.reply_text(
        f"Perfecto {alias}! Antes de elegir la fuente, elegí el *tono* de la nota:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True),
    )
    return ELEGIR_TONO




# ---------------------------
# Elegir tono (personalidad)
# ---------------------------
async def elegir_tono(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = (update.message.text or "").strip()
    key = TONOS_BY_NAME.get(raw_text.lower()) or raw_text.lower()
    if key not in TONOS_LABELS:
        await update.message.reply_text("Tono inválido. Elegí con los botones.")
        return ELEGIR_TONO

    context.user_data["tono"] = key

    botones = [[KeyboardButton(FUENTES_LABELS[k])] for k in
               ("lanacion", "infobae", "ba", "ba_static",
                "caba360", "infoporteo", "noticiascaba")]
    botones.append([KeyboardButton(MANUAL_LABEL)])

    await update.message.reply_text(
        "Listo. Ahora elegí la fuente:",
        reply_markup=ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True),
    )
    return ELEGIR_FUENTE

async def tono_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /tono entusiasta|serio|realista
    Si no pasás nada, te muestra botones.
    """
    args = context.args or []
    if args:
        key = args[0].strip().lower()
        if key in TONOS_LABELS:
            context.user_data["tono"] = key
            await update.message.reply_text(f"✅ Tono guardado para esta sesión: {TONOS_LABELS[key]}")
            return
        await update.message.reply_text("Usá: /tono entusiasta | serio | realista")
        return

    botones = [[KeyboardButton(TONOS_LABELS[k])] for k in ("entusiasta", "serio", "realista")]
    await update.message.reply_text(
        "Elegí el tono:",
        reply_markup=ReplyKeyboardMarkup(botones, resize_keyboard=True, one_time_keyboard=True),
    )

# ---------------------------
# Elegir fuente
# ---------------------------
async def elegir_fuente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw_text = (update.message.text or "").strip()
    raw = raw_text.lower()

    # Modo manual
    if raw_text == MANUAL_LABEL or raw in {
        "agregar noticia a mano",
        "agregar noticia",
        "agregar",
        "cargar a mano",
        "cargar noticia",
    }:
        context.user_data["manual_mode"] = True
        await update.message.reply_text(
            "Modo manual: enviame el *título* de la noticia.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=ReplyKeyboardRemove(),
        )
        return ESPERAR_TITULO

    fuente_key = FUENTES_BY_NAME.get(raw)
    if not fuente_key:
        await update.message.reply_text("Fuente inválida. Elegí con los botones.")
        return ELEGIR_FUENTE

    context.user_data["fuente"] = fuente_key
    await update.message.reply_text(
        f"🔎 Buscando noticias nuevas de *{FUENTES_LABELS[fuente_key]}*…",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=ReplyKeyboardRemove(),
    )

    ok = await _run_scraper(fuente_key)
    path = SALIDAS.get(fuente_key)
    items: List[Dict[str, Any]] = []

    try:
        if ok and path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            items = [i for i in (data or []) if isinstance(i, dict)] if isinstance(data, list) else []
    except Exception as e:
        log.error("Error leyendo %s: %s", path, e)

    if not items:
        await update.message.reply_text(
            f"No encontré noticias para *{FUENTES_LABELS[fuente_key]}*. Probá de nuevo más tarde.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ELEGIR_FUENTE

    context.user_data["items"] = items
    return await mostrar_items(update, context)

async def mostrar_items(update: Update, context: ContextTypes.DEFAULT_TYPE):
    items: List[Dict[str, Any]] = context.user_data.get("items") or []
    if not items:
        await update.message.reply_text("No hay items cargados.")
        return ELEGIR_FUENTE

    used = _load_used_items()
    lines: List[str] = []
    for idx, item in enumerate(items[:10], start=1):
        titulo = _clean_title(item.get("titulo") or "")
        ya = " (YA USADO)" if _item_ya_usada(item, used) else ""
        lines.append(f"{idx}. {_shorten(titulo, 140)}{ya}")

    txt = (
        "Noticias disponibles:\n\n"
        + "\n".join(lines)
        + "\n\nEnviá el *número* de la noticia que quieras trabajar."
    )
    await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
    return ELEGIR_ITEM


# ---------------------------
# Elegir item
# ---------------------------
async def elegir_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    try:
        num = int(text)
    except ValueError:
        await update.message.reply_text("Mandame un número de la lista.")
        return ELEGIR_ITEM

    items: List[Dict[str, Any]] = context.user_data.get("items") or []
    if not (1 <= num <= len(items)):
        await update.message.reply_text("Número fuera de rango.")
        return ELEGIR_ITEM

    item = items[num - 1]
    context.user_data["item"] = item

    # Enriquece con la nota original (copete completo + imagen de portada)
    try:
        from scraping.enrich import enrich_item

        item = enrich_item(item)
        context.user_data["item"] = item
    except Exception as e:
        log.warning("No pude enriquecer la nota: %s", e)

    titulo = _clean_title(item.get("titulo") or "")
    desc = _clean_description(item.get("descripcion") or "")
    base_text = f"{titulo}\n\n{desc}".strip()

    used = _load_used_items()
    h = _hash_title_description(titulo, desc)
    context.user_data["hash_item"] = h
    context.user_data["hash_extra"] = _hashes_extra(item)
    context.user_data["base_text"] = base_text

    if _item_ya_usada(item, used):
        prev = used.get(h) or next(
            (v for v in used.values() if isinstance(v, dict)), {}
        )
        txt = (
            "⚠️ Esta noticia ya fue publicada antes.\n"
            f"- Diario: {prev.get('diario_slug')}\n"
            f"- Fecha: {prev.get('timestamp')}\n\n"
            "¿Querés refritarla y publicarla de nuevo?\n"
            "Respondé *SI* para seguir, o *NO* para elegir otra."
        )
        await update.message.reply_text(txt, parse_mode=ParseMode.MARKDOWN)
        return REUSO_DECISION

    return await _continuar_con_item(update, context)

async def reuso_decision(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    if text not in {"si", "sí", "s", "no", "n"}:
        await update.message.reply_text('Respondé "SI" para republicar, o "NO" para elegir otra.')
        return REUSO_DECISION
    if text in {"no", "n"}:
        await update.message.reply_text("OK, elegí otra noticia de la lista.")
        return ELEGIR_ITEM
    return await _continuar_con_item(update, context)

async def _continuar_con_item(update: Update, context: ContextTypes.DEFAULT_TYPE):
    base_text = context.user_data.get("base_text") or ""
    if not base_text:
        await update.message.reply_text("No tengo texto base para procesar. Volvé a elegir item.")
        return ELEGIR_ITEM

    await update.message.reply_text("🧠 Refritando la nota con IA (Groq)...")

    try:
        loop = asyncio.get_running_loop()
        tono = (context.user_data.get("tono") or DEFAULT_TONO or "serio").strip().lower()
        texto_gemini = await loop.run_in_executor(None, lambda: _procesar_con_gemini(base_text, tono=tono))

        log.info("=== TEXTO RECIBIDO DE GROQ (primeros 500 chars) ===")
        log.info(texto_gemini[:500])
        log.info("===================================================")

        # Intentar parsear con el parser
        try:
            parsed = parse_ai_output(texto_gemini)
            log.info("=== PARSEO EXITOSO ===")
            log.info(f"Título: {parsed.get('titulo')}")
            log.info(f"Cuerpo preview: {str(parsed.get('cuerpo', ''))[:200]}")
        except Exception as parse_error:
            log.error(f"ERROR EN PARSE_AI_OUTPUT: {parse_error}")
            log.error(traceback.format_exc())
            # Fallback: parsear manualmente el JSON
            try:
                data = json.loads(texto_gemini)
                parsed = {
                    "titulo": data.get("titulo", ""),
                    "cuerpo": data.get("cuerpo", ""),
                    "imagen_url": data.get("imagen_url"),
                    "tags": data.get("tags", [])
                }
                log.info("=== PARSEO MANUAL EXITOSO ===")
            except json.JSONDecodeError as json_error:
                log.error(f"ERROR PARSEANDO JSON MANUALMENTE: {json_error}")
                parsed = {"titulo": "", "cuerpo": "", "imagen_url": None, "tags": []}

        titulo_g = (parsed.get("titulo") or "").strip()
        cuerpo_g = (parsed.get("cuerpo") or "").strip()

        log.info(f"Valores extraídos - Título: '{titulo_g[:50]}...', Cuerpo length: {len(cuerpo_g)}")

        def _body_se_ve_mal(txt: str) -> bool:
            t = (txt or "").strip()
            if len(t) < 250:
                return True
            if re.search(r"\s—\s[^\n]{2,60}\s—\s\d", t):
                return True
            if re.search(r"(?i)\bfecha de publicaci", t):
                return True
            if t.endswith("..."):
                return True
            return False

        if not titulo_g:
            titulo_g = _clean_title(context.user_data.get("item", {}).get("titulo") or "")
            log.info(f"Título vacío, usando fallback: {titulo_g[:50]}")
            
        if not cuerpo_g or _body_se_ve_mal(cuerpo_g):
            fallback = context.user_data.get("item", {}).get("descripcion") or base_text
            cuerpo_g = _clean_description(fallback)
            log.info(f"Cuerpo vacío o malo, usando fallback, length: {len(cuerpo_g)}")

        context.user_data["titulo"] = titulo_g
        context.user_data["cuerpo"] = cuerpo_g
        
        log.info("Guardado en user_data, llamando a _mostrar_preview...")
        return await _mostrar_preview(update, context)
        
    except Exception as e:
        log.error(f"ERROR CRÍTICO EN _continuar_con_item: {e}")
        log.error(traceback.format_exc())
        await update.message.reply_text(
            f"❌ Error procesando la noticia:\n```{str(e)[:300]}```",
            parse_mode=ParseMode.MARKDOWN
        )
        return ELEGIR_ITEM


async def _mostrar_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        titulo = context.user_data.get("titulo") or ""
        cuerpo = context.user_data.get("cuerpo") or ""
        imagen_path = context.user_data.get("imagen_path")

        log.info("=== _mostrar_preview ===")
        log.info(f"Título: {titulo[:100]}")
        log.info(f"Cuerpo length: {len(cuerpo)}")
        log.info(f"Imagen path: {imagen_path}")

        # Verificar que tenemos datos
        if not titulo and not cuerpo:
            log.error("No hay título ni cuerpo para mostrar")
            await update.message.reply_text("❌ Error: No se pudo generar la preview. Intentá de nuevo.")
            return ELEGIR_ITEM

        # Limitar longitud para Telegram (máximo 4096 caracteres en total)
        # Dejamos margen para el formato
        max_cuerpo = 3500
        if len(cuerpo) > max_cuerpo:
            cuerpo = cuerpo[:max_cuerpo] + "...\n\n[El texto fue truncado para la preview, pero se publicará completo en WordPress]"

        preview = (
            "📝 *PREVIEW (IA)*\n\n"
            f"*Título:*\n{titulo}\n\n"
            f"*Cuerpo:*\n{cuerpo}\n\n"
        )
        
        if imagen_path:
            preview += "_Hay una imagen cargada para la publicación._\n"
        else:
            preview += "_Se publicará sin imagen (si no cargás una)."

        keyboard = [
            [
                InlineKeyboardButton("✏️ Editar título", callback_data="EDIT_TITULO"),
                InlineKeyboardButton("✏️ Editar cuerpo", callback_data="EDIT_CUERPO"),
            ],
            [InlineKeyboardButton("🖼️ Agregar/editar imagen", callback_data="EDIT_IMG")],
            [
                InlineKeyboardButton("✅ Publicar", callback_data="PUBLICAR"),
                InlineKeyboardButton("❌ Cancelar", callback_data="CANCELAR"),
            ],
        ]

        log.info("Enviando mensaje de preview...")
        await update.message.reply_text(
            preview,
            parse_mode=None,  # CAMBIO: Sin parse_mode para evitar errores de Markdown
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        log.info("Preview enviado correctamente")
        return PREVIEW_EDICION
        
    except Exception as e:
        log.error(f"ERROR EN _mostrar_preview: {e}")
        log.error(traceback.format_exc())
        await update.message.reply_text(
            f"❌ Error mostrando preview:\n```{str(e)[:300]}```",
            parse_mode=ParseMode.MARKDOWN
        )
        return ELEGIR_ITEM


# ---------------------------
# Callbacks de preview (botones) - CORREGIDO
# ---------------------------
async def preview_edicion_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "EDIT_TITULO":
        await query.edit_message_text("Enviame el nuevo título:")
        return ESPERAR_TITULO

    if data == "EDIT_CUERPO":
        await query.edit_message_text("Enviame el nuevo cuerpo de la nota:")
        return ESPERAR_CUERPO

    if data == "EDIT_IMG":
        await query.edit_message_text(
            "Enviame una imagen para asociar (o escribí *sin imagen*).",
            parse_mode=None,  # CAMBIO: Sin parse_mode para evitar errores
        )
        return ESPERAR_IMAGEN

    if data == "PUBLICAR":
        titulo = context.user_data.get("titulo") or ""
        cuerpo = context.user_data.get("cuerpo") or ""
        imagen_path = context.user_data.get("imagen_path")

        # CORREGIDO: Escapar caracteres especiales para MarkdownV2
        titulo_esc = _escape_markdown_v2(titulo)
        cuerpo_esc = _escape_markdown_v2(cuerpo[:500])  # Limitamos para la preview
        
        txt = (
            "✅ *Vista previa final*\n\n"
            f"*Título:*\n{titulo_esc}\n\n"
            f"*Cuerpo:*\n{cuerpo_esc}...\n\n"
        )
        if imagen_path:
            txt += "_Se publicará con imagen._\n"
        else:
            txt += "_Se publicará *sin imagen*._\n"

        txt += "\n¿Publicamos? Respondé *SI* o *NO*."
        
        try:
            await query.edit_message_text(txt, parse_mode=ParseMode.MARKDOWN_V2)
        except Exception as e:
            # Fallback: si falla MarkdownV2, enviar sin formato
            log.warning(f"Error con MarkdownV2, enviando sin formato: {e}")
            txt_simple = (
                "✅ Vista previa final\n\n"
                f"Título:\n{titulo}\n\n"
                f"Cuerpo:\n{cuerpo[:500]}...\n\n"
            )
            if imagen_path:
                txt_simple += "Se publicará con imagen.\n"
            else:
                txt_simple += "Se publicará sin imagen.\n"
            txt_simple += "\n¿Publicamos? Respondé SI o NO."
            await query.edit_message_text(txt_simple, parse_mode=None)
            
        return CONFIRMAR_PUBLICACION

    if data == "CANCELAR":
        await query.edit_message_text("Operación cancelada.")
        return ConversationHandler.END

    return PREVIEW_EDICION


# ---------------------------
# Edición de título / cuerpo / imagen
# ---------------------------
async def esperar_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nuevo = _clean_title(update.message.text or "")
    context.user_data["titulo"] = nuevo
    await update.message.reply_text("Título actualizado ✅")
    return await _mostrar_preview(update, context)

async def esperar_cuerpo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    nuevo = _clean_description(update.message.text or "")
    context.user_data["cuerpo"] = nuevo
    await update.message.reply_text("Cuerpo actualizado ✅")
    return await _mostrar_preview(update, context)

async def esperar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()

    if text in {"sin imagen", "no", "ninguna"}:
        context.user_data.pop("imagen_path", None)
        await update.message.reply_text(
            "OK, se publicará *sin imagen*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return await _mostrar_preview(update, context)

    if update.message.photo:
        photo = update.message.photo[-1]
        file = await photo.get_file()
        UP_DIR.mkdir(parents=True, exist_ok=True)
        file_path = UP_DIR / f"img_{int(time.time())}.jpg"
        await file.download_to_drive(str(file_path))

        if not _es_imagen_aceptable(file_path):
            try:
                file_path.unlink()
            except OSError:
                pass
            await update.message.reply_text("No parece una imagen válida. Probá con otro archivo.")
            return ESPERAR_IMAGEN

        context.user_data["imagen_path"] = str(file_path)
        await update.message.reply_text("Imagen recibida ✅")
        return await _mostrar_preview(update, context)

    await update.message.reply_text(
        "Tenés que mandar una *foto* o escribir *sin imagen*.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ESPERAR_IMAGEN


# ---------------------------
# Confirmar publicación (SI/NO)
# ---------------------------
async def confirmar_publicacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    if text not in {"si", "sí", "s", "no", "n"}:
        await update.message.reply_text('Respondé "SI" para publicar o "NO" para cancelar.')
        return CONFIRMAR_PUBLICACION

    if text in {"no", "n"}:
        await update.message.reply_text("Operación cancelada.")
        return ConversationHandler.END

    titulo = context.user_data.get("titulo") or ""
    cuerpo = context.user_data.get("cuerpo") or ""
    imagen_path = context.user_data.get("imagen_path")

    diario_slug = context.user_data.get("diario_slug")
    diario_cfg = context.user_data.get("diario_cfg") or {}

    if not diario_slug or not diario_cfg:
        await update.message.reply_text("No tengo la config del diario. Volvé a empezar con /start.")
        return ConversationHandler.END

    try:
        featured_media = None
        if imagen_path:
            featured_media = wp_upload_media(diario_cfg, pathlib.Path(imagen_path))

        data = wp_create_post(diario_cfg, titulo, cuerpo, featured_media=featured_media)
    except Exception as e:
        log.error("Error al publicar en WP: %s", e)
        await update.message.reply_text("❌ Error publicando en WordPress. Revisá los logs en el server.")
        return ConversationHandler.END

    # Registrar como usado, pero SIN romper si algo raro pasa
    try:
        used = _load_used_items()
        h = context.user_data.get("hash_item")
        if h:
            registro = {
                "hash": h,
                "diario_slug": diario_slug,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "post_id": data.get("id"),
                "title": titulo,
            }
            used[h] = registro
            for extra in context.user_data.get("hash_extra") or []:
                used.setdefault(extra, registro)
            _save_used_items(used)
    except Exception as e:
        log.error("Error guardando used_items: %s", e)

    link = data.get("link") or data.get("guid", {}).get("rendered") or "(sin link)"
    post_id = data.get("id")
    diario_name = diario_cfg.get("name") or diario_slug

    msg = (
        "✅ Publicado correctamente.\n\n"
        f"{link}\n\n"
        f"Listo. Publicación creada (ID {post_id}) en {diario_name}. "
        "Muchas Gracias por usar el Notibot V2.1! 😊 "
        "Usa /start para volver a iniciar o /help para obtener informacion sobre la version."
    )

    await update.message.reply_text(msg)
    return ConversationHandler.END


# ---------------------------
# Modo manual directo
# ---------------------------
async def agregar_manual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    context.user_data["manual_mode"] = True
    await update.message.reply_text(
        "Modo manual: primero elegí el diario con /start.\n\nLuego, si querés, podés volver a /agregar para título/cuerpo.",
    )
    return ConversationHandler.END


# ---------------------------
# Cambiar imagen de post publicado por el cron
# ---------------------------
async def cambiar_imagen_cron_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Se activa cuando el usuario toca '🖼️ Cambiar imagen' en el mensaje de éxito del cron.
    callback_data = "cambiar_imagen_cron:<post_id>:<diario_slug>"
    """
    query = update.callback_query
    await query.answer()

    parts = (query.data or "").split(":")
    if len(parts) != 3:
        await query.edit_message_text("❌ Callback inválido.")
        return ConversationHandler.END

    _, post_id_str, diario_slug = parts
    try:
        post_id = int(post_id_str)
    except ValueError:
        await query.edit_message_text("❌ post_id inválido.")
        return ConversationHandler.END

    uid = int(update.effective_user.id)
    mis_diarios = diarios_habilitados_para(uid)
    diario_cfg = mis_diarios.get(diario_slug)
    if not diario_cfg:
        await query.edit_message_text("❌ No tenés acceso a ese diario.")
        return ConversationHandler.END

    context.user_data["cron_post_id"]    = post_id
    context.user_data["cron_diario_slug"] = diario_slug
    context.user_data["cron_diario_cfg"]  = diario_cfg

    await query.edit_message_text(
        f"🖼️ <b>Post #{post_id}</b> — enviame la nueva imagen.\n\n"
        "Podés mandar una <b>foto</b> directamente o pegar una <b>URL</b> (debe empezar con http).",
        parse_mode=ParseMode.HTML,
    )
    return CAMBIAR_IMAGEN_CRON


async def esperar_imagen_cron(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Recibe foto o URL, sube a WP Media y actualiza el post del cron."""
    post_id    = context.user_data.get("cron_post_id")
    diario_cfg = context.user_data.get("cron_diario_cfg")

    if not post_id or not diario_cfg:
        await update.message.reply_text(
            "❌ Perdí el contexto. Tocá el botón del mensaje original del cron."
        )
        return ConversationHandler.END

    file_path: Optional[pathlib.Path] = None

    # ── Caso 1: foto enviada al chat ──────────────────────────────────────────
    if update.message.photo:
        photo = update.message.photo[-1]
        file  = await photo.get_file()
        UP_DIR.mkdir(parents=True, exist_ok=True)
        file_path = UP_DIR / f"cron_img_{int(time.time())}.jpg"
        await file.download_to_drive(str(file_path))

    # ── Caso 2: URL pegada como texto ─────────────────────────────────────────
    elif update.message.text:
        url = update.message.text.strip()
        if not url.startswith("http"):
            await update.message.reply_text(
                "No entendí. Mandame una *foto* o pegá una *URL* de imagen (debe empezar con http).",
                parse_mode=ParseMode.MARKDOWN,
            )
            return CAMBIAR_IMAGEN_CRON

        await update.message.reply_text("⏳ Descargando imagen desde la URL...")
        try:
            r = requests.get(url, timeout=20, stream=True)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            ext = ".jpg"
            ct = r.headers.get("Content-Type", "")
            if "png" in ct:    ext = ".png"
            elif "webp" in ct: ext = ".webp"
            UP_DIR.mkdir(parents=True, exist_ok=True)
            file_path = UP_DIR / f"cron_img_{int(time.time())}{ext}"
            with open(file_path, "wb") as fout:
                for chunk in r.iter_content(8192):
                    fout.write(chunk)
        except Exception as e:
            await update.message.reply_text(
                f"❌ No pude descargar la imagen: {e}\nProbá con otra URL o enviá la foto directo."
            )
            return CAMBIAR_IMAGEN_CRON
    else:
        await update.message.reply_text(
            "Mandame una *foto* o una *URL*.", parse_mode=ParseMode.MARKDOWN
        )
        return CAMBIAR_IMAGEN_CRON

    # ── Validar ───────────────────────────────────────────────────────────────
    if not _es_imagen_aceptable(file_path):
        try:
            file_path.unlink()
        except OSError:
            pass
        await update.message.reply_text(
            "El archivo no parece una imagen válida. Probá con otro."
        )
        return CAMBIAR_IMAGEN_CRON

    # ── Subir y actualizar ────────────────────────────────────────────────────
    await update.message.reply_text("⏳ Subiendo imagen a WordPress...")
    try:
        media_id = wp_upload_media(diario_cfg, file_path)
        if not media_id:
            raise RuntimeError("wp_upload_media devolvió None")
        ok = wp_update_post_image(diario_cfg, post_id, media_id)
        if not ok:
            raise RuntimeError("wp_update_post_image falló (ver logs)")
    except Exception as e:
        log.error("Error actualizando imagen del post %s: %s", post_id, e)
        await update.message.reply_text(f"❌ Error al actualizar la imagen:\n{e}")
        return ConversationHandler.END

    await update.message.reply_text(
        f"✅ ¡Imagen actualizada en el post <b>#{post_id}</b>!",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


# ---------------------------
# Cancel
# ---------------------------
async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operación cancelada.", reply_markup=ReplyKeyboardRemove())
    return ConversationHandler.END


# ---------------------------

# ---------------------------
# Error handler (PTB)
# ---------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Evita que el bot muera silenciosamente y avisa al usuario."""
    log.exception("Excepción no manejada en handler", exc_info=context.error)
    try:
        # Si tenemos chat, avisamos con un mensaje corto.
        chat = getattr(update, "effective_chat", None) if update else None
        if chat and getattr(chat, "id", None):
            await context.bot.send_message(
                chat_id=chat.id,
                text=(
                    "⚠️ Se cayó una parte del flujo (error interno).\n"
                    "Probá /start para reiniciar.\n\n"
                    "Si te vuelve a pasar, avisame y lo vemos en logs 🧯"
                ),
            )
    except Exception:
        # No queremos encadenar errores acá
        pass

# build_app / main
# ---------------------------
def build_application() -> Application:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Falta TELEGRAM_TOKEN en el entorno")

    app = ApplicationBuilder().token(token).build()

    # Captura errores de handlers para que no se quede 'colgado' el flujo
    app.add_error_handler(error_handler)

    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("recargar", recargar_cmd))

    # ── Flujo independiente: cambiar imagen de post publicado por el cron ─────
    conv_imagen_cron = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cambiar_imagen_cron_cb, pattern=r"^cambiar_imagen_cron:")
        ],
        states={
            CAMBIAR_IMAGEN_CRON: [
                MessageHandler(
                    (filters.PHOTO | filters.TEXT) & ~filters.COMMAND,
                    esperar_imagen_cron,
                )
            ],
        },
        fallbacks=[
            CommandHandler("cancelar", cancel_cmd),
            CommandHandler("cancel", cancel_cmd),
        ],
        name="conv_cambiar_imagen_cron",
        persistent=False,
        per_chat=True,
        per_user=True,
    )
    app.add_handler(conv_imagen_cron)

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CommandHandler("agregar", agregar_manual),
        ],
        states={
            ELEGIR_DIARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_diario)],
            ELEGIR_TONO: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_tono)],
            ELEGIR_FUENTE: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_fuente)],
            ELEGIR_ITEM: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_item)],
            REUSO_DECISION: [MessageHandler(filters.TEXT & ~filters.COMMAND, reuso_decision)],
            PREVIEW_EDICION: [CallbackQueryHandler(preview_edicion_cb)],
            ESPERAR_TITULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, esperar_titulo)],
            ESPERAR_CUERPO: [MessageHandler(filters.TEXT & ~filters.COMMAND, esperar_cuerpo)],
            ESPERAR_IMAGEN: [MessageHandler((filters.PHOTO | filters.TEXT) & ~filters.COMMAND, esperar_imagen)],
            CONFIRMAR_PUBLICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_publicacion)],
        },
        fallbacks=[
            CommandHandler("cancelar", cancel_cmd),
            CommandHandler("cancel", cancel_cmd),
            CommandHandler("tono", tono_cmd),
        ],
        name="notibot_conv",
        persistent=False,
        per_message=False,
        per_chat=True,
        per_user=True,
    )

    app.add_handler(conv)
    return app

def main():
    app = build_application()
    log.info("Notibot iniciado.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
