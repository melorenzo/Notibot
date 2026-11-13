# scraping/scraper_linkd.py
"""
Scraper de LinkedIn (GCBA) con Selenium.

Por qué cambió tanto: LinkedIn renovó el DOM de la página de empresa y además
la vista /posts/ exige sesión (las cookies del repo están vencidas). La ruta
que sigue funcionando SIN sesión es la página pública de la empresa:

    https://www.linkedin.com/company/<slug>/

Allí cada post es un <article data-activity-urn="urn:li:activity:..."> y el
texto está en <p data-test-id="main-feed-activity-card__commentary">.

El link externo del post (lnkd.in) se resuelve a la URL real (buenosaires.gob.ar,
etc.) para que la nota se publique con el link original y el enriquecimiento
pueda bajar la imagen de portada.

Si la vista pública no devuelve posts, se intenta la sesión (cookies o
usuario/clave) sobre la página /posts/.

Variables de entorno:
  LINKEDIN_START_URL  página pública de la empresa (default GCBA)
  LINKEDIN_POSTS_URL  página de posts (fallback, necesita sesión)
  LINKEDIN_COOKIES    pickle de cookies (default /app/keys/linkedin_cookies.pkl)
  LINKEDIN_EMAIL / LINKEDIN_PASSWORD
  LINKEDIN_MAX_DAYS   antigüedad máxima de un post en días (default 10)
  LINKEDIN_MAX_ITEMS  máximo de posts (default 20)
  LINKEDIN_SCROLL     scrolls para cargar más posts (default 6)
  SELENIUM_REMOTE_URL remote webdriver
  SCRAPER_OUT_PATH    path absoluto del JSON de salida
"""
import datetime
import json
import os
import pickle
import re
import shutil
import sys
import tempfile
import time
import traceback
import unicodedata
from typing import Any, Dict, List, Optional

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

# ── Config ────────────────────────────────────────────────────────────────────
SELENIUM_REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://selenium:4444/wd/hub")
OUT_PATH    = resolve_out("SALIDAS_LINKEDIN", "salidas_linkedin.json")
COOKIES_PATH = os.getenv("LINKEDIN_COOKIES", "/app/keys/linkedin_cookies.pkl")
EMAIL    = os.getenv("LINKEDIN_EMAIL")
PASSWORD = os.getenv("LINKEDIN_PASSWORD")
HEADLESS = os.getenv("HEADLESS", "1") == "1"
DEBUG    = os.getenv("SCRAPER_DEBUG", "1") == "1"

COMPANY  = os.getenv("LINKEDIN_COMPANY", "gobierno-de-la-ciudad-de-buenos-aires")
# Vista pública: funciona sin sesión
START_URL = os.getenv(
    "LINKEDIN_START_URL",
    f"https://www.linkedin.com/company/{COMPANY}/",
)
# Vista de posts: requiere sesión
POSTS_URL = os.getenv(
    "LINKEDIN_POSTS_URL",
    f"https://www.linkedin.com/company/{COMPANY}/posts/?feedView=all",
)

MAX_ITEMS = int(os.getenv("LINKEDIN_MAX_ITEMS", "20"))
MAX_DAYS  = int(os.getenv("LINKEDIN_MAX_DAYS", "10"))
SCROLL    = int(os.getenv("LINKEDIN_SCROLL", "6"))

# Contenedores de cada post (de más nuevo a más viejo)
CARD_SELECTORS = [
    "article[data-activity-urn]",
    "article.main-feed-activity-card",
    "article[data-id='main-feed-card']",
    "div.feed-shared-update-v2",
    "div.occludable-update",
    "[data-urn*='urn:li:activity:']",
]

# Texto del post, dentro de la card
TEXT_SELECTORS = [
    "p[data-test-id='main-feed-activity-card__commentary']",
    ".attributed-text-segment-list__content",
    "[data-test-id*='commentary']",
    "div.update-components-text",
    "span.break-words",
]

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-AR,es;q=0.9",
}

# "23 h", "5 días", "1 semana", "3 meses"
_REL_RE = re.compile(r"(\d+)\s*(h|hr|hrs|min|d|dia|dias|semana|semanas|mes|meses|ano|anos)\b", re.I)
_FOOTER_WORDS = {"recomendar", "comentar", "compartir", "enviar", "guardar", "me gusta"}
_EXT_LINK_RE = re.compile(r'data-tracking-control-name="external_url_click"[^>]*href="([^"]+)"', re.I)
_ANY_URL_RE = re.compile(r'https?://[^\s"\'<>\\]+')

_EXTRACT_JS = """
var cardSel = arguments[0], textSels = arguments[1];
var cards = Array.from(document.querySelectorAll(cardSel));
var out = [];
for (var i = 0; i < cards.length; i++) {
  var card = cards[i];
  var text = '', raw = false;
  for (var j = 0; j < textSels.length; j++) {
    var el = card.querySelector(textSels[j]);
    if (el) {
      var t = (el.innerText || el.textContent || '').trim();
      if (t) { text = t; break; }
    }
  }
  if (!text) { text = (card.innerText || '').trim(); raw = true; }
  var urn = card.getAttribute('data-activity-urn')
         || card.getAttribute('data-urn')
         || card.getAttribute('data-attributed-urn') || '';
  var timeEl = card.querySelector('time');
  var rel = timeEl ? (timeEl.innerText || '').trim() : '';
  var img = '';
  var imgs = card.querySelectorAll('img');
  for (var k = 0; k < imgs.length; k++) {
    var src = imgs[k].currentSrc || imgs[k].getAttribute('src') || '';
    if (!src || src.indexOf('media.licdn.com') === -1) continue;
    if (/company-logo|profile|badge|icon/i.test(src)) continue;
    img = src; break;
  }
  var ext = '';
  var as = card.querySelectorAll('a[href]');
  for (var m = 0; m < as.length; m++) {
    var h = as[m].getAttribute('href') || '';
    if (h.indexOf('lnkd.in') !== -1) { ext = h; break; }
  }
  out.push({urn: urn, text: text, raw: raw, time: rel, img: img, ext: ext});
}
return out;
"""


def log(*a):
    if DEBUG:
        print("[LINKEDIN]", *a, flush=True)


# ── Utilidades ────────────────────────────────────────────────────────────────

def _atomic_write(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_linkedin_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def _browser() -> webdriver.Chrome:
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1360,2600")
    opts.add_argument("--lang=es-AR")
    return webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=opts)


def _dismiss_banners(driver):
    for by, sel in [
        (By.XPATH, "//button[contains(., 'Aceptar') or contains(., 'Accept')]"),
        (By.XPATH, "//button[contains(., 'Permitir') or contains(., 'Allow')]"),
        (By.CSS_SELECTOR, "button[aria-label*='Cerrar'], button[aria-label*='Close']"),
    ]:
        try:
            for el in driver.find_elements(by, sel):
                el.click()
                time.sleep(0.3)
        except Exception:
            pass


def _strip_accents(s: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn"
    )


def _looks_like_date(s: str) -> bool:
    return bool(_REL_RE.fullmatch(_strip_accents((s or "").strip())))


def _parse_rel_date(rel: str) -> Optional[datetime.datetime]:
    m = _REL_RE.search(_strip_accents(rel or ""))
    if not m:
        return None
    n = int(m.group(1))
    u = m.group(2).lower()
    if u in ("h", "hr", "hrs"):
        delta = datetime.timedelta(hours=n)
    elif u == "min":
        delta = datetime.timedelta(minutes=n)
    elif u in ("d", "dia", "dias"):
        delta = datetime.timedelta(days=n)
    elif u.startswith("semana"):
        delta = datetime.timedelta(weeks=n)
    elif u.startswith("mes"):
        delta = datetime.timedelta(days=30 * n)
    elif u.startswith("ano"):
        delta = datetime.timedelta(days=365 * n)
    else:
        return None
    return datetime.datetime.now() - delta


def _clean_text(text: str, raw: bool) -> str:
    """Saca encabezado/pie de la card cuando el texto vino del innerText bruto."""
    text = (text or "").replace(" ", " ")
    if raw:
        lines = [l.strip() for l in text.split("\n")]
        out: List[str] = []
        started = False
        for l in lines:
            if not started:
                if _looks_like_date(l):
                    started = True
                continue
            if l:
                out.append(l)
        text = "\n".join(out)
    # "ver más" / "…ver más"
    text = re.sub(r"[.…]*\s*ver más\s*$", "", text, flags=re.I).strip()
    # pie: reacciones + acciones
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    while lines:
        last = lines[-1].lower()
        if last in _FOOTER_WORDS or last.isdigit() or re.fullmatch(r"\d+\s+comentarios?", last):
            lines.pop()
            continue
        break
    return "\n".join(lines).strip()


def _make_title(text: str) -> str:
    for line in (text or "").split("\n"):
        line = line.strip()
        if line:
            if len(line) > 110:
                line = line[:110].rsplit(" ", 1)[0] + "…"
            return line
    return "Post de LinkedIn"


def _permalink(urn: str) -> str:
    if not urn:
        return ""
    if urn.startswith("urn:"):
        return f"https://www.linkedin.com/feed/update/{urn}/"
    return f"https://www.linkedin.com/feed/update/urn:li:activity:{urn}/"


def _resolve_shortlink(url: str) -> str:
    """lnkd.in -> URL real (buenosaires.gob.ar, etc.)."""
    if not url or "lnkd.in" not in url:
        return url
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.status_code != 200:
            return url
        html = r.text
        m = _EXT_LINK_RE.search(html)
        if m:
            return m.group(1).strip()
        for cand in _ANY_URL_RE.findall(html):
            if "linkedin.com" in cand or "licdn.com" in cand:
                continue
            return cand.rstrip('.,')
        return url
    except Exception as e:
        log(f"No pude resolver {url}: {type(e).__name__}")
        return url


def _clean_link(url: str) -> str:
    if not url:
        return ""
    url = url.split("?")[0].split("#")[0].strip()
    return url


# ── Sesión (opcional: la vista pública no la necesita) ────────────────────────

def _load_cookies_if_any(driver) -> bool:
    if not COOKIES_PATH or not os.path.exists(COOKIES_PATH):
        log("No hay cookies:", COOKIES_PATH)
        return False
    try:
        driver.get("https://www.linkedin.com/")
        time.sleep(1.5)
        with open(COOKIES_PATH, "rb") as f:
            cookies = pickle.load(f)
        added = 0
        for c in cookies:
            c = dict(c)
            c.pop("expiry", None)
            if not c.get("name") or not c.get("value"):
                continue
            dom = c.get("domain") or ".linkedin.com"
            if dom.startswith("."):
                dom = dom[1:]
            c["domain"] = dom
            try:
                driver.add_cookie(c)
                added += 1
            except Exception:
                continue
        log(f"Cargué {added} cookies")
        driver.get("https://www.linkedin.com/feed/")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        _dismiss_banners(driver)
        url = driver.current_url
        if "authwall" in url or "/uas/login" in url or "login" in url:
            log("Cookies vencidas (authwall). Sigo con la vista pública.")
            return False
        log("Sesión válida por cookies")
        return True
    except Exception as e:
        log("Fallo cookies:", e)
        return False


def _login_with_password(driver) -> bool:
    if not (EMAIL and PASSWORD):
        return False
    try:
        driver.get("https://www.linkedin.com/login")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "username")))
        driver.find_element(By.ID, "username").send_keys(EMAIL)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        WebDriverWait(driver, 25).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "nav, #global-nav"))
        )
        _dismiss_banners(driver)
        if COOKIES_PATH:
            with open(COOKIES_PATH, "wb") as f:
                pickle.dump(driver.get_cookies(), f)
            log("Login OK, cookies guardadas:", COOKIES_PATH)
        return True
    except Exception as e:
        log("Login user/pass falló:", e)
        return False


# ── Extracción ────────────────────────────────────────────────────────────────

def _scroll(driver, steps: int = 6, pause: float = 1.0):
    for _ in range(max(0, steps)):
        driver.execute_script("window.scrollBy(0, 1400);")
        time.sleep(pause)


def _collect(driver, url: str) -> List[Dict[str, Any]]:
    driver.get(url)
    WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    _dismiss_banners(driver)
    time.sleep(2)
    log("URL:", driver.current_url[:110], "| title:", driver.title[:60])
    if "authwall" in driver.current_url or "/uas/login" in driver.current_url:
        log("LinkedIn pide sesión para esta URL")
        return []

    for sel in CARD_SELECTORS:
        try:
            raw = driver.execute_script(_EXTRACT_JS, sel, TEXT_SELECTORS)
        except Exception as e:
            log(f"Error extrayendo {sel}: {e}")
            continue
        if raw:
            log(f"cards {sel} -> {len(raw)}")
            _scroll(driver, steps=SCROLL)
            try:
                raw = driver.execute_script(_EXTRACT_JS, sel, TEXT_SELECTORS)
                log(f"cards {sel} (post-scroll) -> {len(raw)}")
            except Exception:
                pass
            return raw
    log("Ninguna card reconocida en", url)
    return []


def _to_items(raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    now = datetime.datetime.now()
    seen_urls: set = set()
    for r in raw:
        text = _clean_text(r.get("text") or "", bool(r.get("raw")))
        if len(text) < 25:
            continue

        urn = r.get("urn") or ""
        rel = r.get("time") or ""
        fecha = _parse_rel_date(rel)
        if fecha is not None and MAX_DAYS > 0 and (now - fecha).days > MAX_DAYS:
            log(f"Descarto post viejo ({rel}): {text[:60]!r}")
            continue

        ext_raw = (r.get("ext") or "").strip()
        ext = _clean_link(ext_raw)
        url_post = _permalink(urn)
        url = _resolve_shortlink(ext_raw) if ext_raw else ""
        if not url:
            url = ext or url_post
        # el shortlink no sirve para el lector: va la URL real
        if ext_raw and url and url != ext_raw:
            text = text.replace(ext_raw, url)
        if ext and ext != ext_raw:
            text = text.replace(ext, url)
        if not url:
            continue

        # dos posts pueden apuntar al mismo link (formularios): el segundo
        # usa el permalink de LinkedIn para no quedar duplicado
        if url in seen_urls:
            url = url_post or url
        if url in seen_urls:
            continue
        seen_urls.add(url)

        items.append({
            "titulo": _make_title(text),
            "descripcion": text,
            "url": url,
            "imagen_url": "",
            "imagen_post": r.get("img") or "",
            "url_linkedin": url_post,
            "url_externo": ext,
            "fecha": fecha.strftime("%Y-%m-%d %H:%M") if fecha else "",
            "fecha_relativa": rel,
            "source": "linkedin",
            "scraped_at": now.isoformat(),
        })
        if len(items) >= MAX_ITEMS:
            break
    return items


def scrape() -> List[Dict[str, Any]]:
    drv = None
    try:
        drv = _browser()

        # 1) Vista pública (sin sesión)
        raw = _collect(drv, START_URL)
        items = _to_items(raw)
        log(f"Vista pública: {len(items)} items")

        # 2) Fallback con sesión sobre /posts/
        if not items:
            has_session = _load_cookies_if_any(drv) or _login_with_password(drv)
            if has_session:
                raw = _collect(drv, POSTS_URL)
                items = _to_items(raw)
                log(f"Vista posts (sesión): {len(items)} items")

        _atomic_write(OUT_PATH, items)
        return items
    finally:
        try:
            if drv is not None:
                drv.quit()
        except Exception:
            pass


def main():
    try:
        items = scrape()
        if not items:
            print(f"AVISO: 0 items en {OUT_PATH}")
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
