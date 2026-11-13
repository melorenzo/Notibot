import os, json, time, pickle, tempfile, shutil, sys, traceback, datetime
from typing import List, Dict, Any

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from paths import resolve_out, ensure_dir  # noqa: E402

# ========= Config =========
SELENIUM_REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://selenium:4444/wd/hub")
OUT_PATH  = resolve_out("SALIDAS_LINKEDIN", "salidas_linkedin.json")
COOKIES_PATH = os.getenv("LINKEDIN_COOKIES", "/app/keys/linkedin_cookies.pkl")
EMAIL = os.getenv("LINKEDIN_EMAIL")
PASSWORD = os.getenv("LINKEDIN_PASSWORD")
HEADLESS = os.getenv("HEADLESS", "1") == "1"
DEBUG   = os.getenv("SCRAPER_DEBUG", "1") == "1"

# Página de posts de la empresa (GCBA, lo podés cambiar por otra empresa)
START_URL = os.getenv(
    "LINKEDIN_START_URL",
    "https://www.linkedin.com/company/gobierno-de-la-ciudad-de-buenos-aires/posts/?feedView=all"
)

# Tu selector prioritario (ABSOLUTO en la página)
PRIORIDAD_TEXTO_ABSOLUTO = [
    "#ember863 > div > div > div.fie-impression-container > div.htWPweFLKEdTDPqkkIKXgEazGTaubUZtRs > div > div > span > span"
]

# Selectores de "card" (contenedor de cada post) en páginas de empresa
CARD_SELECTORS = [
    "article",  # posts modernos suelen venir en <article>
    "div.occludable-update",
    "div.feed-shared-update-v2",
    "[data-urn*='urn:li:activity:']",
]

# Selectores internos para texto del post
TEXT_SELECTORS = [
    "div.update-components-text",
    "div.update-components-text span.break-words",
    "div.feed-shared-update-v2__description-wrapper",
    "div.attributed-text",
    "span.break-words",
    "span[dir='ltr']",
]

# Selectores para link del post (si no, formamos por data-urn)
LINK_SELECTORS = [
    "a[href*='/feed/update/']",
    "a[href*='/posts/']",
]

def log(*a):
    if DEBUG:
        print("[LINKEDIN]", *a, flush=True)

# ========= Utilidades =========
def _browser() -> webdriver.Chrome:
    opts = Options()
    if HEADLESS:
        opts.add_argument("--headless=new")
    opts.add_argument("--no-sandbox")
    opts.add_argument("--disable-dev-shm-usage")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1360,2600")
    return webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=opts)

def _atomic_write_json(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_linkedin_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)

def _save_debug(driver, label: str):
    if not DEBUG:
        return
    try:
        dbg_dir = "/app/debug"
        os.makedirs(dbg_dir, exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        png = os.path.join(dbg_dir, f"linkedin_{label}_{ts}.png")
        html = os.path.join(dbg_dir, f"linkedin_{label}_{ts}.html")
        driver.save_screenshot(png)
        with open(html, "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log("DEBUG:", png, html)
    except Exception as e:
        log("No pude guardar debug:", e)

def _dismiss_banners(driver):
    # cookies / overlays en distintos idiomas
    candidates = [
        # botones típicos de cookies/consent
        (By.XPATH, "//button[contains(., 'Aceptar') or contains(., 'Accept')]"),
        (By.XPATH, "//button[contains(., 'Permitir') or contains(., 'Allow')]"),
        (By.XPATH, "//button[contains(., 'Aceptar todo') or contains(., 'Accept all')]"),
        # modal de sign-in
        (By.CSS_SELECTOR, "button[aria-label*='Cerrar'], button[aria-label*='Close']"),
    ]
    for by, sel in candidates:
        try:
            for el in driver.find_elements(by, sel):
                el.click()
                time.sleep(0.5)
        except Exception:
            pass

def _load_cookies_if_any(driver: webdriver.Chrome) -> bool:
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
            c = dict(c); c.pop("expiry", None)
            if not c.get("name") or not c.get("value"):
                continue
            dom = c.get("domain") or ".linkedin.com"
            if dom.startswith("."): dom = dom[1:]
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
        if "feed" in driver.current_url:
            log("Sesión válida por cookies")
            return True
    except Exception as e:
        log("Fallo cookies:", e)
    return False

def _login_with_password(driver: webdriver.Chrome) -> bool:
    if not (EMAIL and PASSWORD):
        return False
    try:
        driver.get("https://www.linkedin.com/login")
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.ID, "username")))
        driver.find_element(By.ID, "username").send_keys(EMAIL)
        driver.find_element(By.ID, "password").send_keys(PASSWORD)
        driver.find_element(By.CSS_SELECTOR, "button[type=submit]").click()
        WebDriverWait(driver, 25).until(EC.presence_of_element_located((By.CSS_SELECTOR, "nav, #global-nav")))
        _dismiss_banners(driver)
        with open(COOKIES_PATH, "wb") as f:
            pickle.dump(driver.get_cookies(), f)
        log("Login OK, cookies guardadas:", COOKIES_PATH)
        return True
    except Exception as e:
        log("Login user/pass falló:", e)
        return False

def _ensure_session(driver: webdriver.Chrome):
    if _load_cookies_if_any(driver): return
    if _login_with_password(driver): return
    raise RuntimeError("No hay sesión de LinkedIn (ni cookies válidas ni usuario/clave).")

def _scroll(driver, steps=10, pause=1.0):
    for _ in range(steps):
        driver.execute_script("window.scrollBy(0, 1400);")
        time.sleep(pause)

def _inner_text(driver, el) -> str:
    try:
        return driver.execute_script("return arguments[0].innerText || '';", el).strip()
    except Exception:
        try:
            return (el.text or "").strip()
        except Exception:
            return ""

def _extract_from_absolute(driver) -> List[Dict[str, Any]]:
    """Plan B: usar tu selector absoluto en toda la página, y construir items."""
    items = []
    for sel in PRIORIDAD_TEXTO_ABSOLUTO:
        els = driver.find_elements(By.CSS_SELECTOR, sel)
        log(f"ABSOLUTE {sel} -> {len(els)}")
        for el in els:
            txt = _inner_text(driver, el)
            if not txt: continue
            # título: primera línea/oración
            title = txt.split("\n")[0].strip()
            if "." in title: title = title.split(".")[0].strip() + "."
            title = title[:120] or "Post de LinkedIn"
            # url: buscar ascendente con data-urn o link cercano
            url = None
            try:
                container = el
                for _ in range(6):
                    container = container.find_element(By.XPATH, "./..")
                    urn = container.get_attribute("data-urn") or ""
                    if "urn:li:activity:" in urn:
                        url = "https://www.linkedin.com/feed/update/" + urn.split("urn:li:activity:")[-1]
                        break
            except Exception:
                pass
            if not url:
                try:
                    a = el.find_element(By.XPATH, ".//ancestor::article[1]//a[contains(@href,'/feed/update/') or contains(@href,'/posts/')]")
                    url = a.get_attribute("href")
                except Exception:
                    pass
            items.append({"titulo": title, "descripcion": txt, "url": url})
    return items

def _extract_items(driver: webdriver.Chrome, limit=12) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []

    driver.get(START_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    _dismiss_banners(driver)
    time.sleep(1.5)
    _save_debug(driver, "open_company_posts")
    _scroll(driver, steps=10, pause=1.0)
    _save_debug(driver, "after_scroll")

    # 1) Intento con contenedores de cards
    cards = []
    for sel in CARD_SELECTORS:
        found = driver.find_elements(By.CSS_SELECTOR, sel)
        log(f"cards {sel} -> {len(found)}")
        cards.extend(found)

    # Unificar y filtrar duplicados
    seen = set()
    uniq = []
    for c in cards:
        if c.id not in seen:
            seen.add(c.id); uniq.append(c)
    log("uniq cards:", len(uniq))

    # 2) Extraer texto + url desde cada card
    for card in uniq:
        if len(items) >= limit:
            break
        text = ""
        for ts in TEXT_SELECTORS:
            try:
                t = card.find_element(By.CSS_SELECTOR, ts)
                text = _inner_text(driver, t)
                if text: break
            except Exception:
                continue
        if not text:
            # último intento: innerText de la card entera, filtrado
            text = _inner_text(driver, card)
            # heurística: no guardar tarjetas vacías
            if len(text) < 25:
                continue

        # título: primera línea/oración corta
        title = text.split("\n")[0].strip()
        if "." in title: title = title.split(".")[0].strip() + "."
        title = title[:120] or "Post de LinkedIn"

        # URL: por link o por data-urn
        url = None
        for ls in LINK_SELECTORS:
            try:
                a = card.find_element(By.CSS_SELECTOR, ls)
                if a and a.get_attribute("href"):
                    url = a.get_attribute("href"); break
            except Exception:
                continue
        if not url:
            urn = card.get_attribute("data-urn") or ""
            if "urn:li:activity:" in urn:
                url = "https://www.linkedin.com/feed/update/" + urn.split("urn:li:activity:")[-1]

        items.append({"titulo": title, "descripcion": text, "url": url})

    # 3) Si no se encontró nada en cards, probá selector ABSOLUTO
    if not items:
        log("No salieron items desde cards; pruebo selector absoluto prioritario…")
        items = _extract_from_absolute(driver)

    return items[:limit]

def main():
    drv = None
    try:
        drv = _browser()
        # Sesión
        if not _load_cookies_if_any(drv):
            if not _login_with_password(drv):
                raise RuntimeError("No hay sesión de LinkedIn (ni cookies válidas ni usuario/clave).")

        # Extraer
        items = _extract_items(drv, limit=12)
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
        if DEBUG: traceback.print_exc()
        sys.exit(1)
    finally:
        try:
            if drv is not None:
                drv.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
