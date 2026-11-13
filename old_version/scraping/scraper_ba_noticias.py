# scraping/scraper_ba_noticias.py
import os, json, tempfile, shutil, datetime, traceback, sys
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

# OJO: el nombre del archivo DEBE coincidir con el que lee el cron
# (cron_worker_*.py → "salidas_ba_noticias.json"). Antes escribía
# "salidas_ba.json" y el cron leía un archivo inexistente: la fuente
# "BA Noticias" nunca aportó ni una sola publicación.
OUT_PATH  = resolve_out("SALIDAS_BA_NOTICIAS", "salidas_ba_noticias.json")
DEBUG     = os.getenv("SCRAPER_DEBUG", "1") == "1"

BA_LIST_URL = os.getenv("BA_NOTICIAS_URL", "https://buenosaires.gob.ar/noticias")
BA_BASE     = "https://buenosaires.gob.ar"

def log(*a):
    if DEBUG:
        print("[BA]", *a, flush=True)

def _atomic_write_json(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_ba_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)

def _abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return BA_BASE.rstrip("/") + href
    return BA_BASE.rstrip("/") + "/" + href

def _clean(s: str) -> str:
    return " ".join((s or "").split()).strip()

def _fetch(url: str, timeout: int = 25) -> Optional[str]:
    try:
        r = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; Notibot/3.0; +https://fullware.com.ar)"},
        )
        if r.status_code != 200:
            log("HTTP", r.status_code, "en", url)
            return None
        return r.text
    except Exception:
        log("Error fetch:", url)
        log(traceback.format_exc())
        return None

def scrape_ba(max_items: int = 12) -> List[Dict[str, Any]]:
    """
    Scrapea el listado público de noticias del GCBA.
    Devuelve items en formato: {titulo, descripcion, url, ...}
    """
    html = _fetch(BA_LIST_URL)
    if not html:
        _atomic_write_json(OUT_PATH, [])
        return []

    soup = BeautifulSoup(html, "html.parser")

    items: List[Dict[str, Any]] = []

    # Heurística: anchors que apunten a /noticias/... (nota: la página cambia seguido)
    candidates = []
    for a in soup.select("a[href]"):
        href = a.get("href", "")
        if "/noticias" in href:
            if href.rstrip("/") in ("/noticias", "https://buenosaires.gob.ar/noticias"):
                continue
            candidates.append(a)

    # Deduplicar por href manteniendo orden
    seen = set()
    uniq = []
    for a in candidates:
        href = a.get("href", "")
        if href in seen:
            continue
        seen.add(href)
        uniq.append(a)

    for a in uniq:
        if len(items) >= max_items:
            break

        href = _abs(a.get("href", ""))
        title = _clean(a.get_text(" ", strip=True))
        if not title or len(title) < 8:
            continue

        # Intento sacar bajada cercana (p o div)
        desc = ""
        parent = a.parent
        if parent:
            p = parent.find_next("p")
            if p:
                desc = _clean(p.get_text(" ", strip=True))

        # Imagen del card (si está dentro del anchor o de su card)
        img = a.find("img")
        if img is None and parent is not None:
            img = parent.find("img")
        imagen_url = _abs(img.get("src", "")) if img else ""

        items.append(
            {
                "titulo": title,
                "descripcion": desc,
                "url": href,
                "imagen_url": imagen_url,
                "source": "ba",
                "scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
            }
        )

    _atomic_write_json(OUT_PATH, items)
    log("Items:", len(items), "->", OUT_PATH)
    return items

def main():
    items = scrape_ba()
    if not items:
        print(f"AVISO: 0 items en {OUT_PATH}")
        sys.exit(1)
    print(f"OK: Guardado {len(items)} items en {OUT_PATH}")

if __name__ == "__main__":
    main()
