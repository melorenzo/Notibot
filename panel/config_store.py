# panel/config_store.py
"""
Configuración del Notibot: lectura/escritura de diarios.json y del estado del
panel, y guardado de credenciales en config/secrets.env.

Reglas de la casa (PLAN_OPENCODE):
- escritura atómica (temporal + rename) y backup previo con fecha,
- las claves nunca se escriben en diarios.json ni se imprimen en logs,
- secrets.env con permisos 600.
"""
import json
import os
import pathlib
import re
import shutil
import tempfile
import time
import unicodedata
from typing import Any, Dict, List, Optional

ADMIN_TELEGRAM_ID = "6165724246"
TONOS_VALIDOS = ["serio", "entusiasta", "realista", "ironico"]
SLUG_RE = re.compile(r"^[a-z0-9_]{3,40}$")
HORA_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def app_root() -> pathlib.Path:
    env = (os.getenv("APP_ROOT") or "").strip()
    if env and pathlib.Path(env).exists():
        return pathlib.Path(env).resolve()
    return pathlib.Path(__file__).resolve().parent.parent


ROOT = app_root()
DIARIOS_PATH = pathlib.Path(os.getenv("DIARIOS_CONFIG_PATH") or (ROOT / "config" / "diarios.json"))
STATE_PATH = ROOT / "config" / "panel_state.json"
SECRETS_PATH = pathlib.Path(os.getenv("NOTIBOT_SECRETS_ENV") or (ROOT / "config" / "secrets.env"))
BACKUP_DIR = ROOT / "config" / "backups"
LOGS_DIR = ROOT / "logs"


def _cargar_entorno() -> None:
    """variables.env / .env / secrets.env → os.environ (sin pisar lo ya definido)."""
    try:
        from dotenv import load_dotenv

        for nombre in ("variables.env", ".env"):
            ruta = ROOT / nombre
            if ruta.exists():
                load_dotenv(ruta, override=False)
    except Exception:
        pass
    try:
        from core.secrets import load_secrets

        load_secrets()
    except Exception:
        pass


_cargar_entorno()


# ─── Utilidades de archivo ───────────────────────────────────────────────────

def escritura_atomica(path: pathlib.Path, contenido: str, modo: Optional[int] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=path.suffix or ".json", dir=str(path.parent))
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(contenido)
            f.flush()
            os.fsync(f.fileno())
        if modo is not None and os.name != "nt":
            os.chmod(tmp, modo)
        shutil.move(tmp, str(path))
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _backup(path: pathlib.Path) -> Optional[pathlib.Path]:
    if not path.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    destino = BACKUP_DIR / ("%s-%s%s" % (path.stem, time.strftime("%Y%m%d-%H%M%S"), path.suffix))
    try:
        shutil.copy2(path, destino)
        return destino
    except Exception:
        return None


# ─── diarios.json ────────────────────────────────────────────────────────────

def load_diarios() -> Dict[str, Any]:
    if not DIARIOS_PATH.exists():
        return {}
    try:
        return json.loads(DIARIOS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_diarios(data: Dict[str, Any]) -> None:
    _backup(DIARIOS_PATH)
    escritura_atomica(DIARIOS_PATH, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


# ─── panel_state.json ────────────────────────────────────────────────────────

def _slugify(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    base = re.sub(r"[^a-zA-Z0-9]+", "_", base).strip("_").lower()
    return base[:40]


def slugify(texto: str) -> str:
    return _slugify(texto)


def dueno_por_defecto(cfg: Dict[str, Any]) -> str:
    ids = cfg.get("allowed_telegram_ids") or {}
    otros = [nombre for tid, nombre in ids.items() if str(tid) != ADMIN_TELEGRAM_ID]
    if otros:
        return otros[0]
    return "Maty"


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"diarios": {}, "ejecutados": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"diarios": {}, "ejecutados": {}}
    data.setdefault("diarios", {})
    data.setdefault("ejecutados", {})
    return data


def save_state(state: Dict[str, Any]) -> None:
    escritura_atomica(STATE_PATH, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def ensure_state() -> Dict[str, Any]:
    """Completa el estado del panel con los diarios existentes (sin pisar nada)."""
    state = load_state()
    diarios = load_diarios()
    cambios = False
    for slug, cfg in diarios.items():
        if slug in state["diarios"]:
            continue
        state["diarios"][slug] = {
            "owner": dueno_por_defecto(cfg),
            "tonos": ["realista"],
            "horarios": [],
            "gestionado_por": "cron_host",
            "notas_por_corrida": 1,
            "orden_fuentes": [],
            "created_at": "",
        }
        cambios = True
    # diarios borrados a mano del JSON no deben quedar en el estado
    for slug in list(state["diarios"].keys()):
        if slug not in diarios:
            state["diarios"].pop(slug, None)
            cambios = True
    if cambios:
        save_state(state)
    return state


# ─── Credenciales ────────────────────────────────────────────────────────────

def prefijo_env(slug: str) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", slug.upper()).strip("_")
    return (base[:16] or "DIARIO")


def credenciales_configuradas(cfg: Dict[str, Any]) -> bool:
    usuario = os.getenv(cfg.get("username_env") or "", "")
    clave = os.getenv(cfg.get("password_env") or "", "")
    return bool(usuario and clave)


def _leer_secrets() -> List[str]:
    if not SECRETS_PATH.exists():
        return []
    try:
        return SECRETS_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []


def guardar_secretos(pares: Dict[str, str]) -> None:
    """Agrega/reemplaza variables en config/secrets.env (600). No loguea valores."""
    lineas = _leer_secrets()
    pendientes = dict(pares)
    for i, linea in enumerate(lineas):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave = limpia.split("=", 1)[0].strip()
        if clave in pendientes:
            lineas[i] = "%s=%s" % (clave, pendientes.pop(clave))
    if pendientes:
        if lineas and lineas[-1].strip():
            lineas.append("")
        lineas.append("# Diarios dados de alta desde el panel")
        for clave, valor in pendientes.items():
            lineas.append("%s=%s" % (clave, valor))
    escritura_atomica(SECRETS_PATH, "\n".join(lineas) + "\n", modo=0o600)
    for clave, valor in pares.items():
        os.environ[clave] = valor


# ─── Validación de alta ──────────────────────────────────────────────────────

class ErrorValidacion(ValueError):
    pass


def normalizar_url(bruta: str) -> str:
    url = (bruta or "").strip()
    if not url:
        raise ErrorValidacion("Falta el dominio del WordPress.")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    url = url.rstrip("/")
    host = url.split("://", 1)[1].split("/")[0]
    if "." not in host or " " in host:
        raise ErrorValidacion("El dominio no parece válido: %s" % host)
    return url


def parsear_horarios(texto: str) -> List[str]:
    crudos = re.split(r"[,\s;]+", (texto or "").strip())
    horarios = []
    for c in crudos:
        if not c:
            continue
        if not HORA_RE.match(c):
            raise ErrorValidacion("Horario inválido: %s (usá HH:MM, ej 08:30)" % c)
        if c not in horarios:
            horarios.append(c)
    return sorted(horarios)


def parsear_telegram(texto: str) -> List[str]:
    """Uno o varios chat IDs separados por coma, punto y coma o espacio."""
    valor = (texto or "").strip()
    if not valor:
        return []
    ids: List[str] = []
    for parte in re.split(r"[,;\s]+", valor):
        parte = parte.strip()
        if not parte:
            continue
        if not re.match(r"^\d{5,15}$", parte):
            raise ErrorValidacion(
                "Los chat IDs de Telegram deben ser numéricos (5 a 15 dígitos), "
                "separados por coma."
            )
        if parte not in ids:
            ids.append(parte)
    return ids


def crear_diario(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Da de alta un diario: valida, guarda credenciales, escribe diarios.json y
    el estado del panel. Devuelve {slug, entry, credenciales, respaldo}.
    """
    nombre = (payload.get("nombre") or "").strip()
    if len(nombre) < 2:
        raise ErrorValidacion("El nombre del diario es obligatorio.")

    slug = (payload.get("slug") or "").strip().lower() or _slugify(nombre)
    if not SLUG_RE.match(slug):
        raise ErrorValidacion("El slug debe tener entre 3 y 40 caracteres: minúsculas, números o guión bajo.")

    diarios = load_diarios()
    if slug in diarios:
        raise ErrorValidacion("Ya existe un diario con el slug '%s'." % slug)

    url = normalizar_url(payload.get("base_url") or "")
    usuario = (payload.get("usuario") or "").strip()
    clave = (payload.get("password") or "").strip()
    if not usuario or not clave:
        raise ErrorValidacion("Usuario y contraseña de WordPress son obligatorios.")

    tonos = [t for t in (payload.get("tonos") or []) if t in TONOS_VALIDOS] or ["realista"]
    horarios = parsear_horarios(payload.get("horarios") or "")
    dueno = (payload.get("dueno") or "").strip() or "Sin asignar"
    telegram_ids = parsear_telegram(payload.get("telegram_id") or "")
    notas = max(1, min(int(payload.get("notas_por_corrida") or 1), 5))

    prefijo = prefijo_env(slug)
    env_usuario = "%s_USER" % prefijo
    env_clave = "%s_PASS" % prefijo

    ids = {ADMIN_TELEGRAM_ID: "Maty"}
    for cid in telegram_ids:
        ids[cid] = dueno

    entry = {
        "name": nombre,
        "base_url": url,
        "wp_posts_endpoint": "/wp-json/wp/v2/posts",
        "wp_media_endpoint": "/wp-json/wp/v2/media",
        "jwt_token_url": "/wp-json/jwt-auth/v1/token",
        "username_env": env_usuario,
        "password_env": env_clave,
        "allowed_telegram_ids": ids,
        # Avisos de cada publicación: todos los chat IDs cargados en el alta.
        "notify_telegram_ids": list(telegram_ids),
        "default_status": payload.get("default_status") or "publish",
        "default_author": None,
        "default_categories": [],
        "default_tags": [],
        "activo": True,
        "origin": "panel",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }

    guardar_secretos({env_usuario: usuario, env_clave: clave})
    diarios[slug] = entry
    save_diarios(diarios)

    state = ensure_state()
    state["diarios"][slug] = {
        "owner": dueno,
        "owner_telegram_id": telegram_ids[0] if telegram_ids else None,
        "tonos": tonos,
        "horarios": horarios,
        "gestionado_por": "panel",
        "notas_por_corrida": notas,
        "orden_fuentes": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_state(state)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    (LOGS_DIR / ("%s.log" % slug)).touch(exist_ok=True)

    return {"slug": slug, "entry": entry, "credenciales": (env_usuario, env_clave)}


def set_activo(slug: str, activo: bool) -> None:
    diarios = load_diarios()
    if slug not in diarios:
        raise ErrorValidacion("Ese diario no existe.")
    diarios[slug]["activo"] = bool(activo)
    save_diarios(diarios)


def eliminar_diario(slug: str) -> None:
    diarios = load_diarios()
    if slug not in diarios:
        raise ErrorValidacion("Ese diario no existe.")
    diarios.pop(slug)
    save_diarios(diarios)
    state = load_state()
    state["diarios"].pop(slug, None)
    save_state(state)
