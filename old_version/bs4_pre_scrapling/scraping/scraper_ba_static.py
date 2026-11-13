# scraping/scraper_ba_static.py
"""
Scraper para static.buenosaires.gob.ar/noticias
Nueva URL del GCBA (reemplaza a buenosaires.gob.ar/noticias que se cayó).

Estructura HTML de cada card:
  <a href="/gcaba_historico/noticias/slug">
    <img src="/sites/default/files/...jpg" />
    <span class="category">Categoría</span>
    <h4>Título de la noticia</h4>
    <p>Descripción breve...</p>
    <span>Fecha de publicación: DD/MM/YYYY</span>
  </a>

Bonus: descarga la URL de la imagen (Creative Commons, seguro de usar).
"""
import os
import json
import re
import tempfile
import shutil
import datetime
import sys
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

OUT_PATH = resolve_out("SALIDAS_BA_STATIC", "salidas_ba_static.json")
DEBUG    = os.getenv("SCRAPER_DEBUG", "1") == "1"
BASE_URL = "https://static.buenosaires.gob.ar"
LIST_URL = os.getenv("BA_STATIC_URL", f"{BASE_URL}/noticias")
MAX_PAGES = int(os.getenv("BA_STATIC_MAX_PAGES", "3"))  # 9 noticias por página

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Notibot/3.0; +https://fullware.com.ar)",
    "Accept-Language": "es-AR,es;q=0.9",
}


def log(*a):
    if DEBUG:
        print("[BA_STATIC]", *a, flush=True)


def _atomic_write(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_ba_static_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def _abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return BASE_URL.rstrip("/") + "/" + href.lstrip("/")


def _clean(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _fetch(url: str) -> Optional[str]:
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code != 200:
            log(f"HTTP {r.status_code} en {url}")
            return None
        return r.text
    except Exception as e:
        log(f"Error fetch {url}: {e}")
        return None


def _parse_page(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items = []

    # Cada noticia es un <a> con href que contiene /noticias/ o /gcaba_historico/noticias/
    cards = soup.select("a[href*='/noticias/']")

    seen = set()
    for card in cards:
        href = card.get("href", "")
        # Saltar la página de listado en sí
        if href.rstrip("/") in ("/noticias", f"{BASE_URL}/noticias"):
            continue
        if href in seen:
            continue
        seen.add(href)

        # Título: h4 dentro del card
        h4 = card.select_one("h4")
        titulo = _clean(h4.get_text(" ", strip=True)) if h4 else ""
        if not titulo or len(titulo) < 8:
            continue

        # Descripción: primer <p> dentro del card
        p = card.select_one("p")
        descripcion = _clean(p.get_text(" ", strip=True)) if p else ""

        # Categoría: span antes del h4
        spans = card.select("span")
        categoria = ""
        fecha = ""
        for s in spans:
            txt = _clean(s.get_text(" ", strip=True))
            if not txt:
                continue
            if "Fecha" in txt:
                m = re.search(r"(\d{2}/\d{2}/\d{4})", txt)
                if m:
                    fecha = m.group(1)
                continue
            if len(txt) < 60:
                categoria = txt
                break

        # Imagen: img dentro del card
        img = card.select_one("img")
        imagen_url = _abs(img.get("src", "")) if img else ""

        # URL completa de la nota
        url_noticia = _abs(href)

        items.append({
            "titulo": titulo,
            "descripcion": descripcion,
            "categoria": categoria,
            "imagen_url": imagen_url,  # ← CC 2.5 Argentina, libre de usar
            "url": url_noticia,
            "fecha": fecha,
            "source": "ba_static",
            "scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

    return items


def scrape(max_items: int = 0) -> List[Dict[str, Any]]:
    if not max_items:
        max_items = MAX_PAGES * 9
    all_items: List[Dict[str, Any]] = []
    seen_titles = set()

    for page in range(MAX_PAGES):
        url = LIST_URL if page == 0 else f"{LIST_URL}?page={page}"
        log(f"Fetching page {page}: {url}")
        html = _fetch(url)
        if not html:
            log(f"No se pudo obtener página {page}")
            break

        page_items = _parse_page(html)
        log(f"  → {len(page_items)} items en página {page}")

        for item in page_items:
            if len(all_items) >= max_items:
                break
            t = item["titulo"]
            if t not in seen_titles:
                seen_titles.add(t)
                all_items.append(item)

        if len(all_items) >= max_items:
            break

    log(f"Total items: {len(all_items)} → {OUT_PATH}")
    _atomic_write(OUT_PATH, all_items)
    return all_items


def main():
    items = scrape()
    if not items:
        print(f"AVISO: 0 items en {OUT_PATH}")
        sys.exit(1)
    print(f"OK: Guardado {len(items)} items en {OUT_PATH}")


if __name__ == "__main__":
    main()
