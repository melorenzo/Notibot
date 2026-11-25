#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cron_base.py — Lógica común para Cron A y Cron B.
No se ejecuta directamente. Lo importan cron_worker_a.py y cron_worker_b.py.
"""
from __future__ import annotations

import json
import logging
import mimetypes
import os
import pathlib
import random
import subprocess
import sys
import tempfile
import shutil
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app")).resolve()
sys.path.insert(0, str(APP_ROOT))

# httpx y httpcore loguean a INFO la URL completa de cada request. En Telegram
# esa URL es api.telegram.org/bot<TOKEN>/sendMessage, así que a INFO el token del
# bot termina escrito en los logs del cron. Con WARNING sólo se ven los fallos.
for _ruidoso in ("httpx", "httpcore"):
    logging.getLogger(_ruidoso).setLevel(logging.WARNING)

from core.diarios import posts_url, media_url, all_diarios
from core.token_store import get_token_for_diario
from procesing.procesar_gemini import procesar_con_gemini
from procesing.parser_ai import parse_ai_output
from core.image_fetcher import fetch_image
from core.dedup import item_hashes as _item_hashes
from scraping.enrich import enrich_item

TONO_MAP = {
    "serio":      "📰 Serio",
    "entusiasta": "🔥 Entusiasta",
    "realista":   "🧩 Realista",
    "ironico":    "🎭 Irónico",
}

# Modos narrativos disponibles para elección aleatoria (V4)
MODOS_DISPONIBLES = ["informativo", "con_datos", "con_conclusion", "de_opinion"]

# Etiquetas legibles para el mensaje de Telegram
MODOS_LABELS = {
    "informativo":    "📋 Informativo",
    "con_datos":      "📊 Con datos",
    "con_conclusion": "💡 Con conclusión",
    "de_opinion":     "🗣️ De opinión",
}

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_USER_ID = os.getenv("TELEGRAM_USER_ID", "").strip()
UP_DIR           = APP_ROOT / "uploading" / "imagenes_subir"


# ─── Hashing y used_items ─────────────────────────────────────────────────────

def item_hashes(item: Dict[str, Any]) -> Tuple[str, List[str]]:
    """
    Devuelve (hash_legacy, [hash_url, hash_titulo]).

    El hash legacy mantiene la compatibilidad con los used_items.json ya
    generados. Los hashes por URL y por título evitan republicar la misma
    nota cuando la descripción del scrape cambia (muy común: la bajada de
    un portal se reescribe y el hash legacy deja de coincidir).
    """
    return _item_hashes(item)


def load_used(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {e.get("hash", str(i)): e for i, e in enumerate(data) if isinstance(e, dict)}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_used(path: pathlib.Path, data: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_used_", suffix=".json", dir=str(path.parent))
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, str(path))


# ─── Rotación de fuentes ──────────────────────────────────────────────────────

def load_rotation(path: pathlib.Path) -> Dict[str, Any]:
    if not path.exists():
        return {"index": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"index": 0}


def save_rotation(path: pathlib.Path, state: Dict[str, Any]):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def next_source(sources: List[Dict], rotation_path: pathlib.Path) -> Dict[str, Any]:
    """
    Devuelve la fuente que toca según el índice rotativo y avanza el índice.
    El índice se guarda ANTES de usar la fuente, así aunque falle y haya
    fallback, la próxima ejecución arranca desde la siguiente — evita
    que siempre caiga en la misma fuente.
    """
    state = load_rotation(rotation_path)
    idx = state.get("index", 0) % len(sources)
    source = sources[idx]

    # Avanzar y guardar YA, antes de intentar scraping
    state["index"] = (idx + 1) % len(sources)
    state["last_run"] = time.strftime("%Y-%m-%d %H:%M:%S")
    state["last_source"] = source["key"]
    save_rotation(rotation_path, state)

    return source


# ─── Telegram ─────────────────────────────────────────────────────────────────

def tg_send(text: str, log: logging.Logger,
            reply_markup: Optional[dict] = None,
            chat_ids: Optional[List[str]] = None) -> bool:
    """
    Envía a uno o varios chats. Sin `chat_ids` va al admin (TELEGRAM_USER_ID).
    Un destinatario inválido (por ejemplo, alguien que nunca hizo /start) se
    loguea y no corta el resto de los envíos.
    """
    destinos: List[str] = []
    for cid in (chat_ids or []):
        cid = str(cid).strip()
        if cid and cid not in destinos:
            destinos.append(cid)
    if not destinos and TELEGRAM_USER_ID:
        destinos = [TELEGRAM_USER_ID]

    if not TELEGRAM_TOKEN or not destinos:
        log.warning("Telegram no configurado.")
        return False

    todos_ok = True
    for cid in destinos:
        try:
            payload: Dict[str, Any] = {
                "chat_id": cid,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": False,
            }
            if reply_markup:
                payload["reply_markup"] = json.dumps(reply_markup)
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json=payload,
                timeout=15,
            )
            if r.status_code != 200:
                todos_ok = False
                log.warning("Telegram chat %s: HTTP %s (%s)", cid, r.status_code, (r.text or "")[:120])
        except Exception as e:
            todos_ok = False
            log.error("Telegram error con chat %s: %s", cid, e)
    return todos_ok


def tg_success(
    diario_name: str, titulo: str, link: str, fuente_label: str,
    cron_id: str, con_imagen: bool, log: logging.Logger,
    post_id: Optional[int] = None, diario_slug: str = "",
    tono: str = "", modo: str = "",
    notify_ids: Optional[List[str]] = None,
):
    imagen_txt = "🖼️ Con imagen (Unsplash)" if con_imagen else "📄 Sin imagen"

    # Línea de estilo: tono + modo narrativo
    tono_label = TONO_MAP.get(tono, tono) if tono else ""
    modo_label = MODOS_LABELS.get(modo, modo) if modo else ""
    estilo_txt = ""
    if tono_label or modo_label:
        estilo_txt = f"\n🎨 <b>Estilo:</b> {tono_label} + {modo_label}"

    # Botón inline para cambiar imagen
    reply_markup = None
    if post_id and diario_slug:
        reply_markup = {
            "inline_keyboard": [[
                {
                    "text": "🖼️ Cambiar imagen",
                    "callback_data": f"cambiar_imagen_cron:{post_id}:{diario_slug}",
                }
            ]]
        }

    tg_send(
        f"✅ <b>Publicado — Cron {cron_id}</b>\n\n"
        f"📰 <b>Diario:</b> {diario_name}\n"
        f"📌 <b>Título:</b> {titulo}\n"
        f"🔗 {link}\n"
        f"{imagen_txt}"
        f"{estilo_txt}\n\n"
        f"<i>Fuente: {fuente_label} · Notibot V4</i>",
        log,
        reply_markup=reply_markup,
        chat_ids=notify_ids,
    )


def tg_error(msg: str, cron_id: str, log: logging.Logger):
    tg_send(f"❌ <b>Error Cron {cron_id}</b>\n\n{msg}", log)


# ─── Scraping ─────────────────────────────────────────────────────────────────

def run_scraper(source: Dict[str, Any], log: logging.Logger,
                max_retries: int = 3) -> bool:
    """
    Ejecuta el scraper de la fuente.

    - Respeta timeout y cantidad de reintentos por fuente (source["timeout"],
      source["retries"]); si no están, usa los valores globales.
    - Inyecta variables de entorno propias de la fuente (source["env"]).
    - Borra el JSON de salida ANTES de correr, para que nunca se lea un
      archivo viejo cuando el scraper falla (era la causa de que el cron
      publicara "noticias viejas" y luego dijera que no quedaban más).
    - Loguea stdout del scraper: todos los scrapers imprimen sus errores
      ahí, y antes se perdían (por eso los logs mostraban "stderr=" vacío).
    """
    script = APP_ROOT / source["script"]
    if not script.exists():
        log.error("Script no encontrado: %s", script)
        return False

    per_attempt_timeout = int(source.get("timeout") or os.getenv("SCRAPER_TIMEOUT", "180"))
    attempts = int(source.get("retries") or max_retries)

    env = dict(os.environ)
    for k, v in (source.get("env") or {}).items():
        env[str(k)] = str(v)
    # El scraper escribe EXACTAMENTE donde el cron va a leer.
    if source.get("output"):
        env["SCRAPER_OUT_PATH"] = str(source["output"])

    out = source.get("output")
    if out:
        try:
            out = pathlib.Path(out)
            if out.exists():
                out.unlink()
        except Exception as e:
            log.warning("No pude borrar salida vieja de %s: %s", source.get("key"), e)

    for attempt in range(1, attempts + 1):
        log.info("Scraper %s intento %d/%d (timeout=%ds)",
                 source["key"], attempt, attempts, per_attempt_timeout)
        try:
            r = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(APP_ROOT), timeout=per_attempt_timeout,
                capture_output=True, text=True, env=env,
            )
            if r.returncode == 0:
                time.sleep(1.5)  # dar tiempo al filesystem tras shutil.move
                return True
            log.warning(
                "exit=%d stdout=%s stderr=%s",
                r.returncode,
                (r.stdout or "").strip()[-600:],
                (r.stderr or "").strip()[-400:],
            )
        except subprocess.TimeoutExpired:
            log.error("Timeout (%ds) en scraper %s", per_attempt_timeout, source["key"])
        except Exception as e:
            log.error("Error scraper %s: %s", source["key"], e)
        if attempt < attempts:
            retry_wait = int(os.getenv("SCRAPER_RETRY_WAIT", "10"))
            log.info("Esperando %ds antes del reintento...", retry_wait)
            time.sleep(retry_wait)
    return False


def load_items(source: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Lee el JSON de salida de una fuente.

    Antes exigía `size > 2`, pero `json.dump([], indent=2)` pesa EXACTAMENTE
    2 bytes → una lista vacía se confundía con "archivo inexistente".
    Ahora se parsea y se valida que sea una lista no vacía.
    """
    out = source.get("output")
    if not out:
        return []
    out = pathlib.Path(out)
    if not out.exists():
        return []
    try:
        data = json.loads(out.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [i for i in data if isinstance(i, dict)]
    return []


def pick_item(items: List[Dict], used: Dict[str, Any]) -> Optional[Tuple[Dict, str, List[str]]]:
    """Devuelve (item, hash_legacy, hashes_extra) de una noticia no usada."""
    candidates = []
    for item in items:
        titulo = (item.get("titulo") or "").strip()
        if not titulo:
            continue
        legacy, extra = item_hashes(item)
        if legacy in used:
            continue
        if any(h in used for h in extra):
            continue
        candidates.append((item, legacy, extra))
    if not candidates:
        return None
    return random.choice(candidates)


# ─── IA ───────────────────────────────────────────────────────────────────────

def rewrite(item: Dict, tono_key: str, log: logging.Logger) -> Optional[Dict]:
    """
    Llama a Groq con tono + modo narrativo aleatorio.
    Devuelve dict con: titulo, cuerpo, tags, modo (el modo usado).
    """
    titulo = (item.get("titulo") or "").strip()
    desc   = (item.get("descripcion") or "").strip()
    base   = f"{titulo}\n\n{desc}".strip()
    tono_label = TONO_MAP.get(tono_key, "🧩 Realista")

    # Elegir modo narrativo al azar
    modo = random.choice(MODOS_DISPONIBLES)
    modo_label = MODOS_LABELS[modo]
    log.info("Reescribiendo con Groq — tono: %s | modo: %s", tono_label, modo_label)

    try:
        raw = procesar_con_gemini(base, tono=tono_label, modo=modo)
    except Exception as e:
        log.error("Error Groq: %s", e)
        return None

    try:
        parsed = parse_ai_output(raw)
    except Exception:
        try:
            parsed = json.loads(raw)
        except Exception:
            log.error("No pude parsear Groq: %s", raw[:300])
            return None

    titulo_g = (parsed.get("titulo") or "").strip() or titulo
    cuerpo_g = (parsed.get("cuerpo") or "").strip()
    tags_g   = parsed.get("tags") or []

    if not cuerpo_g or len(cuerpo_g) < 200:
        log.warning("Cuerpo corto (%d chars), usando descripción original.", len(cuerpo_g))
        cuerpo_g = desc or base

    return {"titulo": titulo_g, "cuerpo": cuerpo_g, "tags": tags_g, "modo": modo}


# ─── Imagen: Unsplash ─────────────────────────────────────────────────────────

def get_image_for_article(
    tags: List[str],
    item: Dict,
    log: logging.Logger,
) -> Optional[pathlib.Path]:
    UP_DIR.mkdir(parents=True, exist_ok=True)

    imagen_url = (item.get("imagen_url") or "").strip()
    if imagen_url and imagen_url.startswith("http"):
        log.info("Descargando imagen original del artículo: %s", imagen_url)
        try:
            r = requests.get(imagen_url, timeout=20, stream=True)
            if r.status_code == 200:
                ext = ".jpg"
                ct = r.headers.get("Content-Type", "")
                if "png" in ct:
                    ext = ".png"
                elif "webp" in ct:
                    ext = ".webp"
                dest = UP_DIR / f"img_article_{int(time.time())}{ext}"
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(8192):
                        f.write(chunk)
                log.info("Imagen artículo descargada: %s", dest)
                return dest
        except Exception as e:
            log.warning("No pude descargar imagen del artículo: %s", e)

    log.info("Buscando imagen en Unsplash con tags: %s", tags)
    img_path = fetch_image(tags=tags, save_dir=UP_DIR)
    if img_path:
        log.info("Imagen Unsplash: %s", img_path)
        return img_path

    log.warning("Sin imagen disponible para este artículo.")
    return None


# ─── WordPress ────────────────────────────────────────────────────────────────

def wp_upload_media(diario_cfg: Dict, file_path: pathlib.Path, log: logging.Logger) -> Optional[int]:
    if not file_path or not file_path.exists():
        return None
    try:
        token = get_token_for_diario(diario_cfg)
        headers = {"Authorization": f"Bearer {token}"}
        mime, _ = mimetypes.guess_type(str(file_path))
        mime = mime or "image/jpeg"
        url = media_url(diario_cfg)
        with open(file_path, "rb") as f:
            resp = requests.post(url, headers=headers,
                                 files={"file": (file_path.name, f, mime)}, timeout=60)
        if resp.status_code not in (200, 201):
            log.error("Error subiendo imagen: %s %s", resp.status_code, resp.text[:200])
            return None
        media_id = resp.json().get("id")
        log.info("Media subida: ID %s", media_id)
        return media_id
    except Exception as e:
        log.error("Excepción subiendo imagen: %s", e)
        return None


def wp_post(diario_cfg: Dict, titulo: str, cuerpo: str,
            featured_media: Optional[int] = None) -> Dict:
    token = get_token_for_diario(diario_cfg)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload: Dict[str, Any] = {
        "title":   titulo,
        "content": cuerpo,
        "status":  diario_cfg.get("default_status", "publish"),
    }
    for k, wp_k in [("default_categories", "categories"),
                    ("default_tags",       "tags"),
                    ("default_author",     "author")]:
        v = diario_cfg.get(k)
        if v:
            payload[wp_k] = v
    if featured_media:
        payload["featured_media"] = featured_media

    url = posts_url(diario_cfg)
    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"WP {r.status_code}: {r.text[:300]}")
    return r.json()


# ─── Pipeline principal ───────────────────────────────────────────────────────

def run_pipeline(
    cron_id: str,
    diario_slug: str,
    sources: List[Dict[str, Any]],
    rotation_path: pathlib.Path,
    used_path: pathlib.Path,
    tono: str,
    log: logging.Logger,
    max_retries: int = 3,
):
    log.info("=== Notibot V3 — Cron %s iniciando ===", cron_id)

    # 1. Fuente por rotación (va primero en la cascada)
    rotated = next_source(sources, rotation_path)
    log.info("Fuente rotada: %s", rotated["label"])

    # 2. Scraping con fallback en cascada
    # Siempre intenta la fuente rotada primero, luego el resto en orden.
    sources_ordered = [rotated] + [s for s in sources if s["key"] != rotated["key"]]

    used = load_used(used_path)
    pick = None
    source_used = None
    failed_sources: List[Dict[str, Any]] = []
    exhausted_sources: List[str] = []
    skipped_sources: List[str] = []

    for candidate in sources_ordered:
        # Fuentes que necesitan credenciales no configuradas se saltean.
        missing = [v for v in (candidate.get("requires_env") or [])
                   if not (os.getenv(v) or "").strip()]
        if missing:
            log.warning("Fuente %s salteada: falta %s", candidate["key"], ", ".join(missing))
            skipped_sources.append(f"{candidate['label']} (falta {', '.join(missing)})")
            continue

        log.info("Intentando fuente: %s", candidate["label"])
        ok = run_scraper(candidate, log, max_retries)
        items = load_items(candidate)

        if not items:
            log.warning(
                "Fuente %s sin resultados (ok=%s), probando siguiente...",
                candidate["key"], ok,
            )
            failed_sources.append(candidate)
            continue

        # La fuente scrapeó bien: ¿queda alguna nota sin publicar?
        found = pick_item(items, used)
        if found:
            pick = found
            source_used = candidate
            break

        # Traía noticias, pero todas ya fueron publicadas → seguimos con la
        # siguiente fuente en vez de publicar con datos viejos.
        log.info("Todas usadas en %s, probando la siguiente fuente...",
                 candidate["key"])
        exhausted_sources.append(candidate["label"])
        failed_sources.append(candidate)

    if pick is None:
        if exhausted_sources:
            msg = (
                "Todas las noticias disponibles ya fueron publicadas.\n\n"
                f"Fuentes sin notas nuevas: <i>{', '.join(exhausted_sources)}</i>"
            )
        elif not failed_sources and skipped_sources:
            msg = (
                "No hay ninguna fuente disponible.\n\n"
                f"Fuentes salteadas: <i>{', '.join(skipped_sources)}</i>"
            )
        else:
            fallidos_txt = ", ".join(f["label"] for f in failed_sources)
            msg = (
                f"🚨 <b>Cron {cron_id} — Todas las fuentes fallaron</b>\n\n"
                f"Intenté sin éxito:\n<i>{fallidos_txt}</i>\n\n"
                f"No se publicó ninguna noticia."
            )
            if skipped_sources:
                msg += f"\n\n\n<i>Salteadas: {', '.join(skipped_sources)}</i>"
        log.warning(msg)
        tg_error(msg, cron_id, log)
        return

    source = source_used

    if source["key"] != rotated["key"]:
        fallidos_txt = ", ".join(f["label"] for f in failed_sources)
        tg_send(
            f"⚠️ <b>Cron {cron_id} — Fallback activado</b>\n\n"
            f"❌ Sin resultados nuevas en: <i>{fallidos_txt}</i>\n"
            f"✅ Usando en su lugar: <b>{source['label']}</b>",
            log,
        )

    item, item_hash, item_extra = pick
    log.info("Noticia elegida: %s", (item.get("titulo") or "")[:80])

    # 3.5 Enriquecer con la nota original (imagen de portada + copete completo)
    try:
        item = enrich_item(item)
    except Exception as e:
        log.warning("No pude enriquecer la nota: %s", e)

    # 4. Reescribir con Groq
    rewritten = rewrite(item, tono, log)
    if not rewritten:
        tg_error(f"Error IA en: <i>{(item.get('titulo') or '')[:80]}</i>", cron_id, log)
        return

    titulo = rewritten["titulo"]
    cuerpo = rewritten["cuerpo"]
    tags   = rewritten.get("tags") or []
    modo   = rewritten.get("modo") or "informativo"

    # 5. Obtener imagen (artículo original o Unsplash)
    img_path = get_image_for_article(tags=tags, item=item, log=log)

    # 6. Publicar en WordPress
    diarios_cfg = all_diarios()
    cfg = diarios_cfg.get(diario_slug)
    if not cfg:
        tg_error(f"Diario <b>{diario_slug}</b> no encontrado en diarios.json", cron_id, log)
        return

    diario_name = cfg.get("name") or diario_slug
    # Avisos de éxito al dueño del diario (si está configurado); sin lista, van al admin.
    notify_ids = [str(x).strip() for x in (cfg.get("notify_telegram_ids") or []) if str(x).strip()]
    log.info("Publicando en %s...", diario_name)

    try:
        featured_media = None
        if img_path:
            featured_media = wp_upload_media(cfg, img_path, log)

        data = wp_post(cfg, titulo, cuerpo, featured_media=featured_media)
        link    = data.get("link") or data.get("guid", {}).get("rendered") or "(sin link)"
        post_id = data.get("id")
        con_imagen = featured_media is not None

        log.info("✅ Publicado ID %s: %s (imagen: %s)", post_id, link, con_imagen)

        registro = {
            "hash":        item_hash,
            "diario_slug": diario_slug,
            "timestamp":   time.strftime("%Y-%m-%d %H:%M:%S"),
            "post_id":     post_id,
            "title":       titulo,
            "fuente":      source["key"],
            "cron":        cron_id,
            "modo":        "auto_cron",
            "tono":        tono,
            "modo_narrativo": modo,
            "con_imagen":  con_imagen,
        }
        # Guardamos la nota bajo todos sus hashes (legacy + url + título) para
        # que no se repita aunque la descripción del scrape cambie.
        used[item_hash] = registro
        for h in item_extra:
            used.setdefault(h, registro)
        save_used(used_path, used)
        tg_success(diario_name, titulo, link, source["label"], cron_id, con_imagen, log,
                   post_id=post_id, diario_slug=diario_slug, tono=tono, modo=modo,
                   notify_ids=notify_ids)

    except Exception as e:
        log.error("Error WP %s: %s", diario_name, e)
        tg_error(
            f"Error publicando en <b>{diario_name}</b>:\n<code>{str(e)[:300]}</code>",
            cron_id, log,
        )

    log.info("=== Cron %s finalizado ===", cron_id)
