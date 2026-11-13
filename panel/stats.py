# panel/stats.py
"""
Estadísticas del tablero.

Dos fuentes de verdad:
  - WordPress (público, GET /wp-json/wp/v2/posts) → entradas y última fecha real.
  - data/used_items.json → qué publicó el bot, cuándo, con qué fuente.

Para que el tablero no dependa de la latencia de 10+ sitios:
  - las consultas a WordPress se hacen en paralelo y se cachean,
  - si el cache está vencido se devuelve el dato viejo y se refresca en
    segundo plano (stale-while-revalidate),
  - el historial se parsea una sola vez por versión del archivo.
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

from panel import config_store

ROOT = config_store.app_root()
USED_PATH = ROOT / "data" / "used_items.json"

WP_TTL = int(os.getenv("PANEL_WP_TTL", "300"))
WP_TIMEOUT = int(os.getenv("PANEL_WP_TIMEOUT", "5"))
WP_HILOS = int(os.getenv("PANEL_WP_HILOS", "16"))
FORMATO_FECHA = "%Y-%m-%d %H:%M:%S"

_LOCK = threading.Lock()
_WP_CACHE: Dict[str, Dict[str, Any]] = {}
_WP_REFRESCANDO: set = set()
_HIST: Dict[str, Any] = {"clave": None, "filas": [], "por_diario": {}}


# ─── Fechas ─────────────────────────────────────────────────────────────────

def ahora() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(os.getenv("NOTIBOT_TZ", "America/Argentina/Buenos_Aires"))).replace(tzinfo=None)
    except Exception:
        return datetime.now()


def tiempo_relativo(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    seg = (ahora() - dt).total_seconds()
    prefijo = "en " if seg < 0 else "hace "
    seg = abs(seg)
    if seg < 90:
        return "recién"
    if seg < 3600:
        return "%s%d min" % (prefijo, seg / 60)
    if seg < 86400:
        return "%s%d h" % (prefijo, seg // 3600)
    dias = int(seg // 86400)
    if dias == 1:
        return "%s1 día" % prefijo
    if dias < 30:
        return "%s%d días" % (prefijo, dias)
    meses = dias // 30
    return "%s%d mes%s" % (prefijo, meses, "es" if meses > 1 else "")


def _parsear(ts: Any) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts
    if not ts:
        return None
    texto = str(ts).strip().replace("T", " ")
    if texto.endswith("Z"):
        texto = texto[:-1]
    for formato in (FORMATO_FECHA, "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto[:19], formato)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(texto)
    except Exception:
        return None


def fecha_corta(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%d/%m %H:%M" if dt.year == ahora().year else "%d/%m/%y %H:%M")


def proxima_corrida(horarios: List[str]) -> Optional[datetime]:
    """Próximo HH:MM programado (hoy o mañana)."""
    if not horarios:
        return None
    base = ahora()
    candidatos = []
    for h in horarios:
        try:
            hh, mm = [int(x) for x in h.split(":")]
        except Exception:
            continue
        for dia in (0, 1):
            momento = (base + timedelta(days=dia)).replace(hour=hh, minute=mm, second=0, microsecond=0)
            if momento > base:
                candidatos.append(momento)
    return min(candidatos) if candidatos else None


# ─── WordPress ──────────────────────────────────────────────────────────────

def _consultar_wp(base_url: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"ok": False, "posts": None, "ultimo": None, "link": None, "error": None, "ms": None}
    url = (base_url or "").rstrip("/") + "/wp-json/wp/v2/posts?per_page=1&_fields=id,date,link"
    inicio = time.time()
    try:
        resp = requests.get(url, timeout=WP_TIMEOUT, headers={"User-Agent": "Notibot-Panel/1.0"})
        data["ms"] = int((time.time() - inicio) * 1000)
        if resp.status_code == 200:
            data["ok"] = True
            try:
                data["posts"] = int(resp.headers.get("X-WP-Total") or 0)
            except (TypeError, ValueError):
                data["posts"] = None
            items = resp.json()
            if isinstance(items, list) and items:
                data["ultimo"] = _parsear(items[0].get("date"))
                data["link"] = items[0].get("link")
        else:
            data["error"] = "HTTP %d" % resp.status_code
    except Exception as e:
        data["error"] = str(e)[:120]
    return data


def _refrescar_en_segundo_plano(base_url: str) -> None:
    with _LOCK:
        if base_url in _WP_REFRESCANDO:
            return
        _WP_REFRESCANDO.add(base_url)

    def trabajo():
        try:
            data = _consultar_wp(base_url)
            with _LOCK:
                _WP_CACHE[base_url] = {"ts": time.time(), "data": data}
        finally:
            with _LOCK:
                _WP_REFRESCANDO.discard(base_url)

    threading.Thread(target=trabajo, name="wp-refresh", daemon=True).start()


def estado_wp(base_url: str) -> Dict[str, Any]:
    """Estado cacheado del sitio. Nunca bloquea si hay un dato previo."""
    base_url = (base_url or "").rstrip("/")
    if not base_url:
        return {"ok": False, "posts": None, "ultimo": None, "link": None, "error": "sin dominio", "ms": None}

    with _LOCK:
        cache = _WP_CACHE.get(base_url)
    if cache:
        if time.time() - cache["ts"] < WP_TTL:
            return cache["data"]
        _refrescar_en_segundo_plano(base_url)
        return cache["data"]

    data = _consultar_wp(base_url)
    with _LOCK:
        _WP_CACHE[base_url] = {"ts": time.time(), "data": data}
    return data


def consultar_sitios(urls: List[str]) -> Dict[str, Dict[str, Any]]:
    urls = [u for u in dict.fromkeys(urls) if u]
    if not urls:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(WP_HILOS, len(urls)))) as ex:
        return dict(zip(urls, ex.map(estado_wp, urls)))


# ─── Historial del bot ──────────────────────────────────────────────────────

def historial() -> Tuple[List[Dict[str, Any]], Dict[str, List[Dict[str, Any]]]]:
    """(filas ordenadas por fecha, índice por diario). Una fila por nota."""
    if not USED_PATH.exists():
        return [], {}
    stat = USED_PATH.stat()
    clave = (stat.st_mtime_ns, stat.st_size)
    with _LOCK:
        if _HIST["clave"] == clave:
            return _HIST["filas"], _HIST["por_diario"]

    try:
        crudo = json.loads(USED_PATH.read_text(encoding="utf-8"))
    except Exception:
        return [], {}
    registros = crudo if isinstance(crudo, list) else list(crudo.values()) if isinstance(crudo, dict) else []

    vistos = set()
    filas: List[Dict[str, Any]] = []
    por_diario: Dict[str, List[Dict[str, Any]]] = {}
    for r in registros:
        if not isinstance(r, dict):
            continue
        slug = r.get("diario_slug")
        ts = r.get("timestamp") or r.get("fecha")
        if not slug or not ts:
            continue
        marca = (slug, r.get("post_id") or r.get("title"), ts)
        if marca in vistos:
            continue
        vistos.add(marca)
        fila = {
            "slug": slug,
            "titulo": r.get("title") or "(sin título)",
            "fuente": r.get("fuente") or "",
            "post_id": r.get("post_id"),
            "fecha": _parsear(ts),
            "tono": r.get("tono") or "",
        }
        filas.append(fila)
        por_diario.setdefault(slug, []).append(fila)

    filas.sort(key=lambda f: f["fecha"] or datetime.min, reverse=True)
    for lista in por_diario.values():
        lista.sort(key=lambda f: f["fecha"] or datetime.min, reverse=True)

    with _LOCK:
        _HIST.update({"clave": clave, "filas": filas, "por_diario": por_diario})
    return filas, por_diario


def historial_diario(slug: str, limite: int = 20) -> List[Dict[str, Any]]:
    _, por_diario = historial()
    filas = []
    for f in (por_diario.get(slug) or [])[:limite]:
        fila = dict(f)
        fila["hace"] = tiempo_relativo(fila["fecha"])
        fila["fecha_txt"] = fecha_corta(fila["fecha"])
        filas.append(fila)
    return filas


def cola_log(slug: str, lineas: int = 30) -> str:
    """Últimas líneas del log del diario (logs/<slug>.log)."""
    ruta = config_store.LOGS_DIR / ("%s.log" % slug)
    if not ruta.exists():
        return ""
    try:
        with open(str(ruta), "rb") as f:
            f.seek(0, 2)
            tamano = f.tell()
            f.seek(max(0, tamano - 64 * 1024))
            texto = f.read().decode("utf-8", errors="replace")
        return "\n".join(texto.splitlines()[-lineas:])
    except Exception:
        return ""


# ─── Filas ──────────────────────────────────────────────────────────────────

def _salud(activo: bool, wp_ok: bool, creds: bool, meta: Dict[str, Any],
           ultima: Optional[datetime]) -> Tuple[str, str]:
    if not activo:
        return "pausado", "Pausado"
    if not wp_ok:
        return "error", "Sin respuesta"
    if not creds:
        return "warn", "Sin credenciales"
    if meta.get("gestionado_por") == "panel" and not (meta.get("horarios") or []):
        return "warn", "Sin horario"
    if ultima and (ahora() - ultima).total_seconds() > 3 * 86400:
        return "warn", "Sin publicar"
    return "ok", "Operativo"


def _fila(slug: str, cfg: Dict[str, Any], meta: Dict[str, Any], historial_slug: List[Dict[str, Any]],
          wp: Dict[str, Any], hoy, hace_7) -> Dict[str, Any]:
    url = (cfg.get("base_url") or "").rstrip("/")
    activo = bool(cfg.get("activo", True))
    ultima_nb = historial_slug[0] if historial_slug else None
    ultima = ultima_nb["fecha"] if ultima_nb else None
    horarios = meta.get("horarios") or []
    salud, salud_txt = _salud(activo, bool(wp.get("ok")), config_store.credenciales_configuradas(cfg),
                              meta, ultima)
    proxima = proxima_corrida(horarios) if meta.get("gestionado_por") == "panel" and activo else None

    return {
        "slug": slug,
        "nombre": cfg.get("name") or slug,
        "url": url,
        "host": url.replace("https://", "").replace("http://", ""),
        "owner": meta.get("owner") or "Sin asignar",
        "activo": activo,
        "salud": salud,
        "salud_txt": salud_txt,
        "wp_ok": bool(wp.get("ok")),
        "wp_posts": wp.get("posts"),
        "wp_error": wp.get("error"),
        "wp_ultimo": wp.get("ultimo"),
        "wp_ultimo_txt": fecha_corta(wp.get("ultimo")),
        "wp_ultimo_hace": tiempo_relativo(wp.get("ultimo")),
        "wp_ultimo_link": wp.get("link"),
        "nb_ultima": ultima,
        "nb_ultima_txt": fecha_corta(ultima),
        "nb_ultima_hace": tiempo_relativo(ultima) if ultima else "sin registros",
        "nb_titulo": ultima_nb["titulo"] if ultima_nb else None,
        "notas_hoy": sum(1 for f in historial_slug if f["fecha"] and f["fecha"].date() == hoy),
        "notas_7d": sum(1 for f in historial_slug if f["fecha"] and f["fecha"].date() > hace_7),
        "total_nb": len(historial_slug),
        "horarios": horarios,
        "horarios_txt": ", ".join(horarios) if horarios else "cron del host",
        "modo": "Panel" if meta.get("gestionado_por") == "panel" else "Cron host",
        "proxima": proxima,
        "proxima_txt": proxima.strftime("%H:%M") if proxima else "—",
        "notas_por_corrida": meta.get("notas_por_corrida") or 1,
        "tiene_creds": config_store.credenciales_configuradas(cfg),
    }


# ─── API del tablero ────────────────────────────────────────────────────────

def construir_tablero() -> Dict[str, Any]:
    state = config_store.ensure_state()
    diarios = config_store.load_diarios()
    filas_hist, por_diario = historial()

    momento = ahora()
    hoy = momento.date()
    hace_7 = hoy - timedelta(days=7)

    wp = consultar_sitios([(cfg.get("base_url") or "").rstrip("/") for cfg in diarios.values()])

    filas = [
        _fila(slug, cfg, state["diarios"].get(slug, {}), por_diario.get(slug, []),
              wp.get((cfg.get("base_url") or "").rstrip("/"), {}), hoy, hace_7)
        for slug, cfg in diarios.items()
    ]
    filas.sort(key=lambda f: f["nb_ultima"] or datetime.min, reverse=True)

    resumen = {
        "total": len(filas),
        "activos": sum(1 for f in filas if f["activo"]),
        "notas_hoy": sum(f["notas_hoy"] for f in filas),
        "notas_7d": sum(f["notas_7d"] for f in filas),
        "problemas": sum(1 for f in filas if f["salud"] in ("error", "warn")),
        "en_panel": sum(1 for f in filas if f["modo"] == "Panel"),
        "capturado": momento.strftime("%d/%m/%Y %H:%M"),
    }

    recientes = []
    for f in filas_hist[:12]:
        cfg = diarios.get(f["slug"]) or {}
        base = (cfg.get("base_url") or "").rstrip("/")
        recientes.append({
            "slug": f["slug"],
            "diario": cfg.get("name") or f["slug"],
            "titulo": f["titulo"],
            "fuente": f["fuente"],
            "hace": tiempo_relativo(f["fecha"]),
            "post_id": f["post_id"],
            "link": "%s/?p=%s" % (base, f["post_id"]) if f["post_id"] and base else None,
        })

    return {"filas": filas, "resumen": resumen, "recientes": recientes}


def detalle(slug: str) -> Optional[Dict[str, Any]]:
    """Ficha de un diario. Sondea únicamente su WordPress."""
    cfg = config_store.load_diarios().get(slug)
    if not cfg:
        return None
    state = config_store.ensure_state()
    _, por_diario = historial()
    momento = ahora()
    hoy = momento.date()
    url = (cfg.get("base_url") or "").rstrip("/")
    wp = estado_wp(url) if url else {}
    fila = _fila(slug, cfg, state["diarios"].get(slug, {}), por_diario.get(slug, []),
                 wp, hoy, hoy - timedelta(days=7))
    fila["endpoints"] = {
        "posts": cfg.get("wp_posts_endpoint"),
        "media": cfg.get("wp_media_endpoint"),
        "jwt": cfg.get("jwt_token_url"),
    }
    fila["usuario_env"] = cfg.get("username_env")
    fila["clave_env"] = cfg.get("password_env")
    fila["status_defecto"] = cfg.get("default_status")
    fila["telegram"] = cfg.get("allowed_telegram_ids") or {}
    fila["notifica_a"] = cfg.get("notify_telegram_ids") or []
    fila["historial"] = historial_diario(slug, 20)
    fila["log"] = cola_log(slug, 30)
    return fila
