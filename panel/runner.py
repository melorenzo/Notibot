# panel/runner.py
"""
Ejecución de corridas del notibot desde el panel.

Los scrapers escriben archivos compartidos (`salidas_*.json`, Selenium remoto)
y el historial de deduplicación es único, así que las corridas van **en cola y
de a una**: dos diarios a la misma hora no se pisan. El scheduler sólo encola;
un único worker ejecuta.
"""
import json
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

from panel import config_store

ROOT = config_store.app_root()
RUNS_PATH = ROOT / "data" / "panel_runs.json"
LOGS_DIR = ROOT / "logs"
MAX_COLA = 12

_estado_lock = threading.Lock()
_cola: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
_encolados: List[str] = []
_actual: Optional[Dict[str, Any]] = None
_worker: Optional[threading.Thread] = None
_scheduler: Optional[threading.Thread] = None
_parar = threading.Event()


def ahora() -> datetime:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(os.getenv("NOTIBOT_TZ", "America/Argentina/Buenos_Aires"))).replace(tzinfo=None)
    except Exception:
        return datetime.now()


# ─── Historial de corridas en disco ─────────────────────────────────────────

def _leer() -> Dict[str, Any]:
    if not RUNS_PATH.exists():
        return {"last": {}, "history": []}
    try:
        data = json.loads(RUNS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last": {}, "history": []}
    data.setdefault("last", {})
    data.setdefault("history", [])
    return data


def _guardar(data: Dict[str, Any]) -> None:
    data["history"] = data.get("history", [])[-200:]
    config_store.escritura_atomica(RUNS_PATH, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _registrar(registro: Dict[str, Any]) -> None:
    data = _leer()
    data["last"][registro["slug"]] = registro
    data.setdefault("history", []).append(registro)
    try:
        _guardar(data)
    except Exception:
        pass


def estado_corridas() -> Dict[str, Any]:
    data = _leer()
    with _estado_lock:
        actual = dict(_actual) if _actual else None
        cola = list(_encolados)
    data["actual"] = actual
    data["cola"] = cola
    data["running"] = [actual["slug"]] if actual else []
    return data


def esta_corriendo(slug: str) -> bool:
    with _estado_lock:
        if _actual and _actual["slug"] == slug:
            return True
        return slug in _encolados


# ─── Worker serial ──────────────────────────────────────────────────────────

def _comando(slug: str, notas: int) -> List[str]:
    return [sys.executable, str(ROOT / "cron_worker.py"), slug,
            "--notas", str(max(1, min(int(notas or 1), 5))), "--ahora"]


def _ejecutar(trabajo: Dict[str, Any]) -> int:
    slug = trabajo["slug"]
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / ("%s.log" % slug)

    entorno = dict(os.environ)
    entorno["APP_ROOT"] = str(ROOT)
    entorno["PYTHONUNBUFFERED"] = "1"
    entorno["PYTHONIOENCODING"] = "utf-8"

    kwargs: Dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        kwargs["start_new_session"] = True

    with open(str(log_path), "a", encoding="utf-8") as log:
        log.write("\n===== corrida %s (%s) =====\n" % (ahora().strftime("%Y-%m-%d %H:%M:%S"), trabajo["motivo"]))
        log.flush()
        proc = subprocess.Popen(_comando(slug, trabajo["notas"]), cwd=str(ROOT), env=entorno,
                                stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **kwargs)
    return proc.wait()


def _worker_loop() -> None:
    global _actual
    while True:
        trabajo = _cola.get()
        if trabajo is None:
            _cola.task_done()
            return
        slug = trabajo["slug"]
        inicio = ahora()
        with _estado_lock:
            _actual = {"slug": slug, "inicio": inicio, "pid": None, "motivo": trabajo["motivo"]}
            if slug in _encolados:
                _encolados.remove(slug)
        try:
            codigo = _ejecutar(trabajo)
        except Exception as e:
            print("[panel] no pude ejecutar %s: %s" % (slug, e), file=sys.stderr)
            codigo = -1
        fin = ahora()
        with _estado_lock:
            _actual = None
        _registrar({
            "slug": slug,
            "inicio": inicio.strftime("%Y-%m-%d %H:%M:%S"),
            "fin": fin.strftime("%Y-%m-%d %H:%M:%S"),
            "duracion_s": int((fin - inicio).total_seconds()),
            "codigo": codigo,
            "ok": codigo == 0,
            "motivo": trabajo["motivo"],
        })
        _cola.task_done()


def _asegurar_worker() -> None:
    global _worker
    with _estado_lock:
        if _worker and _worker.is_alive():
            return
        _worker = threading.Thread(target=_worker_loop, name="notibot-worker", daemon=True)
        _worker.start()


# ─── API ────────────────────────────────────────────────────────────────────

def encolar(slug: str, notas: int = 1, motivo: str = "manual") -> Dict[str, Any]:
    """Encola una corrida. Devuelve {ok, mensaje}."""
    if not config_store.load_diarios().get(slug):
        return {"ok": False, "mensaje": "Ese diario no existe."}
    with _estado_lock:
        if (slug in _encolados) or (_actual and _actual["slug"] == slug):
            return {"ok": False, "mensaje": "Ya hay una corrida en curso o en cola para este diario."}
        if len(_encolados) >= MAX_COLA:
            return {"ok": False, "mensaje": "La cola está llena (%d). Probá en unos minutos." % MAX_COLA}
        _encolados.append(slug)
        posicion = len(_encolados)
    _cola.put({"slug": slug, "notas": notas, "motivo": motivo})
    _asegurar_worker()
    if posicion == 1:
        return {"ok": True, "mensaje": "Corrida encolada, arranca ahora. Mirá el log en la ficha del diario."}
    return {"ok": True, "mensaje": "Corrida encolada en la posición %d; arranca cuando termine la anterior." % posicion}


# Alias retrocompatible con las rutas del panel
def start(slug: str, notas: int = 1, motivo: str = "manual") -> Dict[str, Any]:
    return encolar(slug, notas, motivo)


# ─── Scheduler ──────────────────────────────────────────────────────────────

def tick() -> List[str]:
    """Encola los diarios cuya hora coincide con ahora. Devuelve los encolados."""
    lanzados: List[str] = []
    state = config_store.ensure_state()
    diarios = config_store.load_diarios()
    momento = ahora()
    slot = momento.strftime("%H:%M")
    hoy = momento.date().isoformat()

    for slug, meta in list(state["diarios"].items()):
        if meta.get("gestionado_por") != "panel":
            continue
        cfg = diarios.get(slug)
        if not cfg or not cfg.get("activo", True):
            continue
        if slot not in (meta.get("horarios") or []):
            continue
        clave = "%s|%s|%s" % (slug, hoy, slot)
        if clave in state["ejecutados"]:
            continue
        if esta_corriendo(slug):
            continue
        resultado = encolar(slug, meta.get("notas_por_corrida") or 1, motivo="horario %s" % slot)
        if resultado.get("ok"):
            state["ejecutados"][clave] = momento.strftime("%Y-%m-%d %H:%M:%S")
            lanzados.append(slug)

    if lanzados:
        limite = momento.timestamp() - 7 * 86400
        for clave in list(state["ejecutados"].keys()):
            try:
                ts = datetime.strptime(state["ejecutados"][clave], "%Y-%m-%d %H:%M:%S").timestamp()
            except Exception:
                ts = 0
            if ts < limite:
                state["ejecutados"].pop(clave, None)
        config_store.save_state(state)
    return lanzados


def _loop() -> None:
    while not _parar.is_set():
        try:
            tick()
        except Exception as e:  # el scheduler nunca debe morir
            print("[panel] error en scheduler: %s" % e, file=sys.stderr)
        _parar.wait(20)


def iniciar_scheduler() -> bool:
    global _scheduler
    if os.getenv("PANEL_SCHEDULER", "1") == "0":
        return False
    with _estado_lock:
        if _scheduler and _scheduler.is_alive():
            return True
        _parar.clear()
        _scheduler = threading.Thread(target=_loop, name="notibot-scheduler", daemon=True)
        _scheduler.start()
    return True


def scheduler_activo() -> bool:
    return bool(_scheduler and _scheduler.is_alive())


def detener() -> None:
    _parar.set()
    _cola.put(None)
