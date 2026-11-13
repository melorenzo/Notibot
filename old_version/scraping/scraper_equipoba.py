# scraping/scraper_equipoba.py
"""
Scraper para equipoba.buenosaires.gob.ar/app/billboard

SPA React con login propio. Flujo:
  1. Ir a /app/billboard → redirige al login
  2. Completar CUIL + contraseña
  3. Esperar redirect de vuelta al billboard
  4. Extraer cards de noticias
"""
import os
import json
import time
import tempfile
import shutil
import datetime
import traceback
import sys
from typing import List, Dict, Any

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

SELENIUM_REMOTE_URL = os.getenv("SELENIUM_REMOTE_URL", "http://selenium:4444/wd/hub")
OUT_PATH  = os.getenv("SALIDAS_EQUIPOBA", "/app/salidas_equipoba.json")
HEADLESS  = os.getenv("HEADLESS", "1") == "1"
DEBUG     = os.getenv("SCRAPER_DEBUG", "1") == "1"
START_URL = os.getenv("EQUIPOBA_URL", "https://equipoba.buenosaires.gob.ar/app/billboard")

EQUIPOBA_USER = os.getenv("EQUIPOBA_USER", "")
EQUIPOBA_PASS = os.getenv("EQUIPOBA_PASS", "")


def log(*a):
    if DEBUG:
        print("[EQUIPOBA]", *a, flush=True)


def _browser() -> webdriver.Chrome:
    o = Options()
    if HEADLESS:
        o.add_argument("--headless=new")
    o.add_argument("--no-sandbox")
    o.add_argument("--disable-dev-shm-usage")
    o.add_argument("--disable-gpu")
    o.add_argument("--window-size=1440,2200")
    o.add_argument("--lang=es-AR")
    return webdriver.Remote(command_executor=SELENIUM_REMOTE_URL, options=o)


def _atomic_write(path: str, data: Any):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_equipoba_", suffix=".json",
                               dir=os.path.dirname(path) or ".")
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)


def _save_debug(driver, label: str):
    if not DEBUG:
        return
    try:
        os.makedirs("/app/debug", exist_ok=True)
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        driver.save_screenshot(f"/app/debug/equipoba_{label}_{ts}.png")
        with open(f"/app/debug/equipoba_{label}_{ts}.html", "w", encoding="utf-8") as f:
            f.write(driver.page_source)
        log(f"debug guardado: equipoba_{label}_{ts}")
    except Exception as e:
        log("debug error:", e)


def _clean(s: str) -> str:
    return " ".join((s or "").split()).strip()


def _do_login(driver) -> bool:
    if not EQUIPOBA_USER or not EQUIPOBA_PASS:
        log("EQUIPOBA_USER / EQUIPOBA_PASS no configurados en .env")
        return False

    _save_debug(driver, "login_page")

    # Paso 1: click en "Acceder en este navegador"
    for by, sel in [
        (By.XPATH, "//*[contains(text(),'Acceder en este navegador')]"),
        (By.XPATH, "//button[contains(text(),'Acceder')]"),
        (By.XPATH, "//a[contains(text(),'Acceder')]"),
        (By.CSS_SELECTOR, "button.btn-primary"),
        (By.CSS_SELECTOR, "a.btn-primary"),
    ]:
        try:
            btn = WebDriverWait(driver, 8).until(EC.element_to_be_clickable((by, sel)))
            btn.click()
            log(f"Click en 'Acceder': {by}={sel}")
            time.sleep(3.0)
            _save_debug(driver, "after_acceder_click")
            break
        except Exception:
            continue

    # Paso 2: completar usuario
    user_field = None
    for by, sel in [
        (By.NAME, "username"),
        (By.NAME, "cuil"),
        (By.NAME, "user"),
        (By.ID,   "username"),
        (By.ID,   "cuil"),
        (By.CSS_SELECTOR, "input[placeholder*='uil']"),
        (By.CSS_SELECTOR, "input[placeholder*='suario']"),
        (By.CSS_SELECTOR, "input[type='text']"),
        (By.CSS_SELECTOR, "input[type='email']"),
    ]:
        try:
            user_field = WebDriverWait(driver, 4).until(
                EC.presence_of_element_located((by, sel))
            )
            log(f"Campo usuario: {by}={sel}")
            break
        except Exception:
            continue

    if not user_field:
        log("No encontré campo de usuario")
        _save_debug(driver, "no_user_field")
        return False

    # Paso 3: completar contraseña
    pass_field = None
    for by, sel in [
        (By.NAME, "password"),
        (By.ID,   "password"),
        (By.CSS_SELECTOR, "input[type='password']"),
    ]:
        try:
            pass_field = driver.find_element(by, sel)
            log(f"Campo contraseña: {by}={sel}")
            break
        except Exception:
            continue

    if not pass_field:
        log("No encontré campo de contraseña")
        return False

    user_field.clear()
    user_field.send_keys(EQUIPOBA_USER)
    time.sleep(0.4)
    pass_field.clear()
    pass_field.send_keys(EQUIPOBA_PASS)
    time.sleep(0.4)
    _save_debug(driver, "login_filled")

    # Paso 4: submit
    for by, sel in [
        (By.CSS_SELECTOR, "button[type='submit']"),
        (By.CSS_SELECTOR, "input[type='submit']"),
        (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'ingres')]"),
        (By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'entrar')]"),
        (By.XPATH, "//button"),
    ]:
        try:
            driver.find_element(by, sel).click()
            log(f"Submit: {by}={sel}")
            break
        except Exception:
            continue

    # ── FIX PRINCIPAL ──────────────────────────────────────────────────────────
    # Esperar que el callback OAuth complete y estemos de vuelta en equipoba.
    # Keycloak redirige a /callback primero, luego la SPA navega a /app/billboard.
    # No alcanza con "landing not in url" — hay que esperar el dominio correcto.
    EQUIPOBA_DOMAIN = "equipoba.buenosaires.gob.ar"
    log("Esperando redirect OAuth de vuelta a equipoba...")
    try:
        WebDriverWait(driver, 40).until(
            lambda d: EQUIPOBA_DOMAIN in d.current_url
                      and "/landing" not in d.current_url
                      and "/callback" not in d.current_url
        )
        log(f"OAuth completado → {driver.current_url}")
    except Exception:
        log(f"Timeout esperando OAuth. URL actual: {driver.current_url}")
        _save_debug(driver, "oauth_timeout")
        return False

    # Dar tiempo a que la SPA hidrate el DOM
    time.sleep(4.0)
    _save_debug(driver, "login_success")

    # Navegar explícitamente al billboard por las dudas
    if "/app/billboard" not in driver.current_url:
        log("Navegando explícitamente a /app/billboard")
        driver.get("https://equipoba.buenosaires.gob.ar/app/billboard")
        time.sleep(4.0)

    _save_debug(driver, "billboard")
    return True


SKIP_TITLES = {
    "cartelera", "beneficios", "atajos", "calendario", "recibos",
    "asistencia y gestión", "búsquedas internas", "instituto superior",
    "ver todo", "seguir leyendo", "ver detalle", "más", "carrera",
    "hola", "calendario de licencias y fichadas", "categorías",
    "buscar artículos", "todas las novedades",
}

def _is_noticia(titulo: str, descripcion: str) -> bool:
    t = titulo.strip().lower()
    if not t or len(t) < 10:
        return False
    for skip in SKIP_TITLES:
        if t == skip or t.startswith(skip):
            return False
    return True

def _extract(html: str, limit: int = 12) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()

    # La cartelera de equipoba: cada noticia tiene h2 + "Seguir leyendo" + img
    # Los títulos son h2, NO h3 (h3 son los títulos de la sidebar)
    for h in soup.select("h4"):
        if len(items) >= limit:
            break
        titulo = _clean(h.get_text(" ", strip=True))
        if not titulo or titulo in seen:
            continue
        # Buscar descripción: párrafo en el padre
        parent = h.parent
        descripcion = ""
        if parent:
            p = parent.find("p", class_="description") or parent.find("p")
            if p:
                descripcion = _clean(p.get_text(" ", strip=True))
        if not descripcion:
            sib = h.find_next_sibling("p")
            if sib:
                descripcion = _clean(sib.get_text(" ", strip=True))

        if not _is_noticia(titulo, descripcion):
            continue
        seen.add(titulo)

        img = parent.find("img") if parent else None
        imagen_url = img.get("src", "") if img else ""

        a = parent.find("a", href=True) if parent else None
        url = a.get("href", "") if a else ""
        if url and not url.startswith("http"):
            url = "https://equipoba.buenosaires.gob.ar" + url

        items.append({
            "titulo": titulo,
            "descripcion": descripcion,
            "imagen_url": imagen_url,
            "url": url,
            "source": "equipoba",
            "scraped_at": datetime.datetime.utcnow().isoformat() + "Z",
        })

    log(f"Items válidos: {len(items)}")
    return items[:limit]


def main():
    drv = _browser()
    try:
        log(f"Abriendo {START_URL}")
        drv.get(START_URL)
        time.sleep(2.0)

        # Detectar si estamos en login
        needs_login = (
            "billboard" not in drv.current_url or
            len(drv.find_elements(By.CSS_SELECTOR, "input[type='password']")) > 0
        )
        if needs_login:
            log(f"URL actual: {drv.current_url} — haciendo login...")
            if not _do_login(drv):
                log("Login fallido")
                _atomic_write(OUT_PATH, [])
                sys.exit(1)
        else:
            log("Billboard accesible sin login")

        # Esperar contenido
        try:
            WebDriverWait(drv, 20).until(
                lambda d: len(d.find_elements(By.CSS_SELECTOR, "h1,h2,h3,article,div.card")) > 2
            )
        except Exception:
            log("Timeout esperando contenido, continuando...")

        time.sleep(2.0)

        for _ in range(4):
            drv.execute_script("window.scrollBy(0, 1200);")
            time.sleep(0.8)
        _save_debug(drv, "scrolled")

        items = _extract(drv.page_source, limit=12)
        log(f"Items extraídos: {len(items)}")
        _atomic_write(OUT_PATH, items or [])
        print(f"OK: Guardado {len(items)} items en {OUT_PATH}")

    except Exception as e:
        log("ERROR:", e)
        if DEBUG:
            traceback.print_exc()
        try:
            _atomic_write(OUT_PATH, [])
        except Exception:
            pass
        sys.exit(1)
    finally:
        try:
            drv.quit()
        except Exception:
            pass


if __name__ == "__main__":
    main()
