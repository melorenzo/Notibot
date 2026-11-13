# scraping/paths.py
"""
Resolución centralizada de rutas para los scrapers.

Problema que resuelve: los scrapers usaban `os.makedirs(os.path.dirname(path))`
con rutas relativas (p.ej. "salidas_lanacion.json"), y `dirname` devuelve ""
en ese caso → FileNotFoundError y el scraper moría sin escribir el JSON.
Además el path de salida del scraper y el que lee el cron no coincidían.
"""
import os
import pathlib


def app_root() -> pathlib.Path:
    """Raíz de la app. Si APP_ROOT no existe (run local), usa el cwd."""
    root = pathlib.Path(os.getenv("APP_ROOT", "/app"))
    if root.exists():
        return root.resolve()
    return pathlib.Path.cwd().resolve()


def resolve_out(env_var: str, default_name: str) -> str:
    """
    Devuelve un path ABSOLUTO de salida, en este orden de prioridad:

    1. SCRAPER_OUT_PATH — lo inyecta cron_base/bot con el path exacto que
       va a leer el cron. Así scraper y cron nunca se desincronizan (fue
       lo que rompió la fuente "BA Noticias" durante meses).
    2. La variable específica del scraper (SALIDAS_LANACION, etc.).
    3. default_name dentro de app_root().

    Si el valor es relativo, se resuelve contra app_root().
    """
    val = (os.getenv("SCRAPER_OUT_PATH") or "").strip()
    if not val:
        val = (os.getenv(env_var) or "").strip()
    if val:
        p = pathlib.Path(val)
        if not p.is_absolute():
            p = app_root() / p
        return str(p)
    return str(app_root() / default_name)


def ensure_dir(path: str) -> str:
    """Crea el directorio padre del path (si hay) y lo devuelve."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    return d
