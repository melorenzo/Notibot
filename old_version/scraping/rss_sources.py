# scraping/rss_sources.py
"""
Registro de las fuentes con feed RSS.

Todas se scrapean con el mismo script genérico (scraping/scraper_rss.py),
que se configura por variables de entorno. Este módulo evita duplicar la
configuración en los 4 cron workers y en el bot de Telegram.
"""
import os
import pathlib

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT", "/app"))
if not APP_ROOT.exists():
    APP_ROOT = pathlib.Path.cwd()

# key, label, sitio, feed
RSS_SOURCES = [
    {
        "key": "caba360",
        "label": "CABA 360",
        "site": "https://caba360.com.ar/",
        "feed": "https://caba360.com.ar/feed.xml",
    },
    {
        "key": "infoporteo",
        "label": "Infoporteño",
        "site": "https://xn--infoporteo-19a.com.ar/",
        "feed": "https://xn--infoporteo-19a.com.ar/feed.xml",
    },
    {
        "key": "noticiascaba",
        "label": "Noticias CABA",
        "site": "https://noticiascaba.com.ar/",
        "feed": "https://noticiascaba.com.ar/feed.xml",
    },
]

RSS_SCRIPT = "scraping/scraper_rss.py"


def output_path(key: str, app_root=None) -> pathlib.Path:
    root = pathlib.Path(app_root or APP_ROOT)
    return root / f"salidas_{key}.json"


def rss_source(key: str, app_root=None) -> dict:
    """Devuelve el dict de fuente (mismo formato que usan los cron workers)."""
    cfg = next((s for s in RSS_SOURCES if s["key"] == key), None)
    if cfg is None:
        raise KeyError(f"Fuente RSS desconocida: {key}")

    out = output_path(cfg["key"], app_root)
    return {
        "key": cfg["key"],
        "label": cfg["label"],
        "script": RSS_SCRIPT,
        "output": out,
        "env": {
            "RSS_SOURCE_KEY": cfg["key"],
            "RSS_SITE_URL": cfg["site"],
            "RSS_FEED_URL": cfg["feed"],
            "RSS_OUT_PATH": str(out),
        },
        "timeout": 60,
        "retries": 2,
    }


def rss_sources(keys=None, app_root=None) -> list:
    keys = keys or [s["key"] for s in RSS_SOURCES]
    return [rss_source(k, app_root) for k in keys]
