# scraping/scraper_infobae.py
"""
Scraper de Infobae (tag Ciudad de Buenos Aires).

Antes usaba Selenium contra el nodo remoto. Eso lo dejaba sin noticias
cuando el contenedor de Selenium no estaba disponible o tardaba, y era la
fuente que más veces figuraba como fallida en los logs.

El listado se sirve renderizado en el HTML: con `requests` se obtienen las
mismas cards. Si vuelve a hacer falta JS, se activa el fallback con
INFOBAE_USE_SELENIUM=1.
"""
import os, json, time, tempfile, shutil, datetime, sys, traceback, re
from typing import List, Dict, Any, Optional

import requests
from bs4 import BeautifulSoup

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

SELENIUM_REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://selenium:4444/wd/hub")
OUT_PATH  = resolve_out("SALIDAS_INFOBAE", "salidas_infobae.json")
HEADLESS  = os.getenv("HEADLESS", "1") == "1"
DEBUG     = os.getenv("SCRAPER_DEBUG", "1") == "1"
USE_SELENIUM = os.getenv("INFOBAE_USE_SELENIUM", "0") == "1"
START_URL = os.getenv(
    "INFOBAE_URL",
    "https://www.infobae.com/tag/ciudad-de-buenos-aires/"
)
TIMEOUT = int(os.getenv("INFOBAE_TIMEOUT", "25"))
BASE = "https://www.infobae.com"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-AR,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def log(*a):
    if DEBUG:
        print("[INFOBAE]", *a, flush=True)


def _atomic_write_json(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_infobae_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def _abs(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http"):
        return href
    return BASE + (href if href.startswith("/") else "/" + href)


def _tx(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s or "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _fetch_html() -> Optional[str]:
    try:
        r = requests.get(START_URL, headers=HDRS, timeout=TIMEOUT)
        if r.status_code != 200:
            log("HTTP", r.status_code)
            return None
        return r.text
    except Exception as e:
        log("Error fetch:", e)
        return None


def _fetch_html_selenium() -> Optional[str]:
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.common.by import By

        o = Options()
        if HEADLESS:
            o.add_argument("--headless=new")
        o.add_argument("--no-sandbox")
        o.add_argument("--disable-dev-shm-usage")
        o.add_argument("--disable-gpu")
        o.add_argument("--window-size=1440,2200")

        drv = webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=o)
        try:
            drv.get(START_URL)
            WebDriverWait(drv, 25).until(
                EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1.0)
            for _ in range(6):
                drv.execute_script("window.scrollBy(0, 1400);")
                time.sleep(0.9)
            return drv.page_source
        finally:
            try:
                drv.quit()
            except Exception:
                pass
    except Exception as e:
        log("Error Selenium:", e)
        return None


def _extract_items(html: str, limit=12) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []
    seen = set()

    cards = soup.select("a.feed-list-card, div.feed-list-card, article.card")
    for c in cards:
        if len(items) >= limit:
            break
        h = c.select_one("h2.feed-list-card-headline-lean, h2.headline, h2, h3")
        d = c.select_one("h3.deck, p, .excerpt, .summary")
        title = _tx(h.get_text(" ", strip=True))[:140] if h else ""
        desc = _tx(d.get_text(" ", strip=True))[:800] if d else ""

        link = c.select_one("a[href]") if c.name != "a" else c
        href = link.get("href", "") if link else ""
        url = _abs(href) if href else ""

        if not title and not desc:
            continue
        if not title and desc:
            title = (desc.split(".")[0] + ".")[:120]
        if not title or title in seen:
            continue
        seen.add(title)

        img = c.find("img")
        imagen_url = _abs(img.get("src", "")) if img and img.get("src") else ""

        items.append({
            "titulo": title,
            "descripcion": desc,
            "url": url,
            "imagen_url": imagen_url,
            "source": "infobae",
            "scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

    # Fallback: headlines sueltos
    if len(items) < 4:
        hs = soup.select("h2.feed-list-card-headline-lean")
        ds = soup.select("h3.deck")
        for i in range(min(len(hs), len(ds))):
            if len(items) >= limit:
                break
            ti = _tx(hs[i].get_text(" ", strip=True))[:140]
            de = _tx(ds[i].get_text(" ", strip=True))[:800]
            if ti and ti not in seen:
                seen.add(ti)
                items.append({"titulo": ti, "descripcion": de, "url": "",
                              "imagen_url": "", "source": "infobae"})

    return items[:limit]


def main():
    try:
        html = _fetch_html()
        if not html and USE_SELENIUM:
            html = _fetch_html_selenium()
        if not html:
            _atomic_write_json(OUT_PATH, [])
            print(f"ERROR: no se pudo obtener {START_URL}")
            sys.exit(1)

        items = _extract_items(html, limit=12)
        log("items extraídos:", len(items))
        _atomic_write_json(OUT_PATH, items or [])
        if not items:
            print(f"AVISO: 0 items en {OUT_PATH}")
            sys.exit(1)
        print(f"OK: Guardado {len(items)} items en {OUT_PATH}")
    except Exception as e:
        try:
            _atomic_write_json(OUT_PATH, [])
        except Exception:
            pass
        print("ERROR:", e)
        if DEBUG:
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
