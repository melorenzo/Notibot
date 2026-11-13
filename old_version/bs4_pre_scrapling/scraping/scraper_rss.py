# scraping/scraper_rss.py
"""
Scraper genérico para sitios que publican un feed RSS/Atom.

No necesita librerías extra: parsea el XML con la stdlib (xml.etree).

Configuración por variables de entorno (las inyecta cron_base / bot_telegram):
  RSS_SOURCE_KEY  clave de la fuente  (ej. "caba360")
  RSS_SITE_URL    URL del sitio (se usa para buscar las imágenes de portada)
  RSS_FEED_URL    URL del feed RSS
  RSS_OUT_PATH    path ABSOLUTO del JSON de salida
  RSS_MAX_ITEMS   máximo de items (default 30)
  RSS_TIMEOUT     timeout HTTP en segundos (default 25)

Flujo:
  1. Descarga el feed.
  2. Parsea items (RSS 2.0 o Atom).
  3. Descarga el home del sitio y mapea url_de_nota -> imagen_url.
  4. Escribe el JSON de forma atómica.

Si el feed no está disponible, cae al HTML del home y extrae las cards.
"""
import datetime
import html as html_lib
import json
import os
import re
import sys
import tempfile
import shutil
import time
import traceback
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

SOURCE_KEY = (os.getenv("RSS_SOURCE_KEY") or "rss").strip().lower()
SITE_URL   = (os.getenv("RSS_SITE_URL") or "").strip()
FEED_URL   = (os.getenv("RSS_FEED_URL") or "").strip()
OUT_PATH   = resolve_out("RSS_OUT_PATH", f"salidas_{SOURCE_KEY}.json")
MAX_ITEMS  = int(os.getenv("RSS_MAX_ITEMS", "30"))
TIMEOUT    = int(os.getenv("RSS_TIMEOUT", "25"))
DEBUG      = os.getenv("SCRAPER_DEBUG", "1") == "1"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9",
}


def log(*a):
    if DEBUG:
        print(f"[RSS:{SOURCE_KEY}]", *a, flush=True)


def _clean(s: str) -> str:
    s = html_lib.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return " ".join(s.split()).strip()


def _atomic_write(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=f".tmp_rss_{SOURCE_KEY}_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def _abs(base: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if not base:
        return href
    return urllib.parse.urljoin(base, href)


def _get(url: str) -> Optional[str]:
    if not url:
        return None
    try:
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if r.status_code != 200:
            log(f"HTTP {r.status_code} en {url}")
            return None
        # Sin charset declarado, requests decodifica como ISO-8859-1 y los
        # acentos salen rotos ("Ã¡"). Estos sitios mandan UTF-8.
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = r.apparent_encoding or "utf-8"
        return r.text
    except Exception as e:
        log(f"Error fetch {url}: {type(e).__name__}: {e}")
        return None


# ── Parsing del feed ──────────────────────────────────────────────────────────

def _local(tag: str) -> str:
    return tag.split("}", 1)[1].lower() if "}" in tag else tag.lower()


def _child(node, *names: str):
    wanted = {n.lower() for n in names}
    for ch in list(node):
        if _local(ch.tag) in wanted:
            return ch
    return None


def _child_text(node, *names: str) -> str:
    ch = _child(node, *names)
    if ch is None:
        return ""
    if ch.text:
        return ch.text.strip()
    # Atom: <content type="html">...</content>
    return "".join(ch.itertext()).strip()


def _link_of(node) -> str:
    link = _child(node, "link")
    if link is not None:
        if link.text and link.text.strip():
            return link.text.strip()
        href = link.get("href")
        if href:
            rel = (link.get("rel") or "alternate").lower()
            if rel in ("alternate", ""):
                return href.strip()
    guid = _child(node, "guid", "id")
    if guid is not None:
        val = (guid.text or "").strip()
        if val.startswith("http"):
            return val
    return ""


def _parse_feed(xml_text: str) -> List[Dict[str, Any]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log(f"XML inválido: {e}")
        return []

    raw_items: List[Dict[str, Any]] = []
    for node in root.iter():
        tag = _local(node.tag)
        if tag not in ("item", "entry"):
            continue

        titulo = _clean(_child_text(node, "title"))
        if not titulo:
            continue

        descripcion = _clean(_child_text(node, "description", "summary", "content", "encoded"))
        link = _link_of(node)
        categoria = _clean(_child_text(node, "category"))
        pub_date = _child_text(node, "pubdate", "published", "updated", "date")

        if not link:
            continue

        raw_items.append({
            "titulo": titulo,
            "descripcion": descripcion,
            "url": link,
            "categoria": categoria,
            "pub_date": pub_date,
        })

    return raw_items


# ── Fallback: parsear el HTML del home ───────────────────────────────────────

def _parse_html(html: str, base_url: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    seen = set()

    selectors = [
        "article.vv-card", "article.card", "div.vv-card", "article",
        "div.post", "li.post", "article.entry", "div.entry",
    ]
    cards = []
    for sel in selectors:
        cards = soup.select(sel)
        if len(cards) >= 3:
            break

    if not cards:
        cards = soup.select("a[href]")

    for c in cards:
        a = c.select_one("a[href]") if c.name != "a" else c
        if a is None:
            continue
        href = a.get("href", "")
        if not href or href.startswith("#"):
            continue

        t = c.select_one("h1, h2, h3, h4") or (c if c.name in ("h1", "h2", "h3", "h4") else None)
        titulo = _clean(t.get_text(" ", strip=True)) if t else _clean(a.get_text(" ", strip=True))
        if len(titulo) < 12:
            continue

        d = c.select_one("p.vv-card__dek, .dek, .excerpt, .summary, p")
        descripcion = _clean(d.get_text(" ", strip=True)) if d else ""

        url = _abs(base_url, href)
        if url in seen:
            continue
        seen.add(url)

        items.append({
            "titulo": titulo,
            "descripcion": descripcion,
            "url": url,
            "categoria": "",
            "pub_date": "",
        })

    return items


# ── Imágenes ──────────────────────────────────────────────────────────────────

def _site_images(html: str, base_url: str) -> Dict[str, str]:
    """Mapea url_de_nota -> url_de_imagen usando los <a> que envuelven <img>."""
    soup = BeautifulSoup(html, "html.parser")
    mapping: Dict[str, str] = {}
    for a in soup.select("a[href]"):
        img = a.find("img")
        if img is None:
            continue
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        if not src:
            continue
        url = _abs(base_url, a.get("href", ""))
        if url and url not in mapping:
            mapping[url] = _abs(base_url, src)
    return mapping


def _article_image(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for prop in ("og:image", "twitter:image"):
        tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


# ── Main ──────────────────────────────────────────────────────────────────────

def scrape() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    used_feed = False

    if FEED_URL:
        xml_text = _get(FEED_URL)
        if xml_text:
            items = _parse_feed(xml_text)
            used_feed = bool(items)
            log(f"Feed {FEED_URL}: {len(items)} items")

    if not items and SITE_URL:
        html = _get(SITE_URL)
        if html:
            items = _parse_html(html, SITE_URL)
            log(f"HTML {SITE_URL}: {len(items)} items")

    if not items:
        log("Sin items.")
        _atomic_write(OUT_PATH, [])
        return []

    # Enriquecimiento con imágenes del home
    images: Dict[str, str] = {}
    if SITE_URL:
        home = _get(SITE_URL)
        if home:
            images = _site_images(home, SITE_URL)

    out: List[Dict[str, Any]] = []
    for item in items[:MAX_ITEMS]:
        img = images.get(item["url"], "")
        if not img and os.getenv("RSS_FETCH_ARTICLE_IMAGES", "0") == "1":
            art_html = _get(item["url"])
            if art_html:
                img = _article_image(art_html)
                time.sleep(0.5)
        out.append({
            "titulo": item["titulo"],
            "descripcion": item["descripcion"],
            "url": item["url"],
            "imagen_url": img,
            "categoria": item.get("categoria", ""),
            "pub_date": item.get("pub_date", ""),
            "source": SOURCE_KEY,
            "scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

    _atomic_write(OUT_PATH, out)
    log(f"OK: {len(out)} items → {OUT_PATH} (feed={used_feed}, con_imagen={sum(1 for i in out if i['imagen_url'])})")
    return out


def main():
    try:
        items = scrape()
        if not items:
            print(f"AVISO: 0 items para {SOURCE_KEY} ({FEED_URL or SITE_URL})")
            sys.exit(1)
        print(f"OK: Guardado {len(items)} items en {OUT_PATH}")
    except Exception as e:
        try:
            _atomic_write(OUT_PATH, [])
        except Exception:
            pass
        print("ERROR:", e)
        if DEBUG:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
