# scraping/dom.py
"""
Capa fina sobre Scrapling para los scrapers HTML.

Dos ganancias concretas frente a BeautifulSoup:

1. **Selectores adaptativos**: la primera vez que un selector encuentra algo se
   guarda una huella del elemento (`auto_save=True`). Si el portal rediseña el
   HTML y el selector deja de devolver resultados, Scrapling busca por esa
   huella (`adaptive=True`) en vez de devolver cero noticias.
2. **Parser más rápido** (lxml + orjson) y con API tipo Scrapy/Parsel.

El storage de huellas vive en `data/scrapling/<scraper>.db`, así que persiste
entre corridas y no ensucia el home del container. Si el archivo se borra, el
scraper sigue funcionando: simplemente vuelve a aprender.
"""
import logging
import os
import pathlib
from typing import Optional

from scrapling.core.storage import SQLiteStorageSystem
from scrapling.parser import Selector

APP_ROOT = pathlib.Path(os.getenv("APP_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
if not APP_ROOT.exists():  # corrida local fuera de /app
    APP_ROOT = pathlib.Path.cwd()

ADAPTATIVO = os.getenv("SCRAPER_ADAPTATIVO", "1") == "1"

# Scrapling avisa por cada intento de reubicación que no supera el umbral; en
# una corrida normal eso llena el log del cron sin aportar nada.
logging.getLogger("scrapling").setLevel(logging.ERROR)


def storage_path(nombre: str) -> pathlib.Path:
    return APP_ROOT / "data" / "scrapling" / ("%s.db" % nombre)


def parsear(html: str, nombre: str, url: str = "") -> "Selector":
    """Parsea HTML con storage adaptativo por scraper."""
    ruta = storage_path(nombre)
    try:
        ruta.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return Selector(
        html or "",
        url=url or "",
        adaptive=ADAPTATIVO,
        storage=SQLiteStorageSystem,
        storage_args={"storage_file": str(ruta)},
    )


def css(nodo, selector: str, adaptativo: bool = True):
    """Todos los elementos que matchean. Guarda huella si es la primera vez."""
    usar = adaptativo and ADAPTATIVO
    return nodo.css(selector, adaptive=usar, auto_save=usar)


def primero(nodo, selector: str, adaptativo: bool = True) -> Optional["Selector"]:
    encontrados = css(nodo, selector, adaptativo)
    return encontrados[0] if len(encontrados) else None


def limpiar(texto) -> str:
    return " ".join((str(texto) if texto is not None else "").split()).strip()


def texto(nodo) -> str:
    """Texto normalizado, uniendo nodos hijos con espacio (no pega palabras)."""
    if nodo is None:
        return ""
    try:
        partes = nodo.css("::text").getall()
    except Exception:
        partes = []
    if partes:
        return limpiar(" ".join(p or "" for p in partes))
    return limpiar(getattr(nodo, "text", ""))


def attr(nodo, nombre: str) -> str:
    if nodo is None:
        return ""
    try:
        return (nodo.attrib.get(nombre) or "").strip()
    except Exception:
        return ""


def subir(nodo, tag: str) -> Optional["Selector"]:
    """Primer ancestro con ese tag (equivalente a find_parent)."""
    actual = nodo
    for _ in range(12):
        if actual is None:
            return None
        try:
            if getattr(actual, "tag", None) == tag:
                return actual
            actual = actual.parent
        except Exception:
            return None
    return None
