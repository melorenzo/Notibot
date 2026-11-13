# scraping/scraper_lanacion.py
import os, json, re, sys, tempfile, shutil, traceback
from typing import List, Dict, Any
import requests

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import dom  # noqa: E402  (Scrapling + selectores adaptativos)
from paths import resolve_out, ensure_dir  # noqa: E402

# resolve_out() evita el bug de las rutas relativas: con
# SALIDAS_LANACION=salidas_lanacion.json, os.path.dirname() devolvía "" y
# os.makedirs("") tiraba FileNotFoundError → el scraper moría sin escribir.
OUT_PATH  = resolve_out("SALIDAS_LANACION", "salidas_lanacion.json")
START_URL = os.getenv(
    "LANACION_URL",
    "https://www.lanacion.com.ar/tema/ciudad-de-buenos-aires-tid58873/"
)
DEBUG   = os.getenv("SCRAPER_DEBUG", "1") == "1"
TIMEOUT = int(os.getenv("LANACION_TIMEOUT", "15"))
BASE    = "https://www.lanacion.com.ar"

HDRS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-AR,es;q=0.9",
    "Cache-Control": "no-cache",
}

def log(*a):
    if DEBUG: print("[LANACION]", *a, flush=True)

def _tx(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s or "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()

def _atomic_write_json(path: str, data: Any):
    d = ensure_dir(path)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_lanacion_", suffix=".json", dir=d)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    shutil.move(tmp, path)

def _is_junk(desc: str) -> bool:
    """Detecta si la descripción es solo metadatos (fecha, categoría) y no contenido real."""
    if not desc or len(desc) < 40:
        return True
    # Patrón típico de basura: "Categoría — Sección — 15 de marzo de 2026"
    if re.search(r"\d{1,2} de \w+ de \d{4}$", desc):
        return True
    if desc.count(" — ") >= 2:
        return True
    return False

def _fetch_article_lead(url: str) -> str:
    """
    Entra al artículo y extrae el primer párrafo real (lead/copete).
    Solo se llama si el listado no trae descripción útil.
    """
    if not url or not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers=HDRS, timeout=TIMEOUT)
        if r.status_code != 200:
            return ""
        page = dom.parsear(r.text, "lanacion", url=url)

        # Selectores comunes de La Nación para el copete/lead
        for sel in [
            "p.com-lead",
            "h2.com-subtitle",
            "div.body-nota p",
            "div.article-body p",
            "div.nota-content p",
            "p.summary",
            "article p",
        ]:
            el = dom.primero(page, sel)
            if el:
                text = _tx(dom.texto(el))
                # Asegurar que es contenido real y no otro metadato
                if len(text) > 60 and not _is_junk(text):
                    return text[:600]
    except Exception as e:
        log(f"Error fetch artículo: {e}")
    return ""

def _extract(html: str, limit=12) -> List[Dict[str, Any]]:
    page = dom.parsear(html, "lanacion", url=START_URL)
    items: List[Dict[str, Any]] = []
    seen = set()
    urls = set()

    # Recolectar anchors con título + URL
    anchors = dom.css(page, "section.mod-description h2.com-title a.com-link")
    if not len(anchors):
        anchors = dom.css(page, "h2.com-title a.com-link, h3.com-title a.com-link")

    for a in anchors:
        if len(items) >= limit:
            break

        title = _tx(dom.texto(a))[:140]
        if not title or title in seen:
            continue
        seen.add(title)

        href = dom.attr(a, "href")
        if href and not href.startswith("http"):
            href = BASE + href
        if href and href in urls:
            continue

        # Intentar lead dentro del listado
        lead = _tx(dom.texto(dom.primero(a, "span.com-lead")))

        # Si no hay lead útil en el listado, buscar en el artículo
        if _is_junk(lead):
            log(f"  Sin lead útil, entrando al artículo: {href[:60]}...")
            lead = _fetch_article_lead(href)

        # Si sigue sin lead, usar el título como base (al menos Groq tiene algo)
        if not lead:
            lead = title

        if href:
            urls.add(href)
        items.append({"titulo": title, "descripcion": lead, "url": href})

    # Fallback si el selector principal no trajo nada
    if len(items) < 4:
        cards = dom.css(page, "article, li.com-item, div.com-item")
        for c in cards:
            if len(items) >= limit:
                break
            t = (dom.primero(c, "h2.com-title a.com-link") or
                 dom.primero(c, "h3.com-title a.com-link") or
                 dom.primero(c, "h2, h3"))
            d = (dom.primero(c, ".com-lead") or
                 dom.primero(c, ".com-subtitle") or
                 dom.primero(c, "p"))
            title = _tx(dom.texto(t))[:140] if t else ""
            desc  = _tx(dom.texto(d))[:600] if d else ""

            if not title or title in seen:
                continue
            link = dom.subir(t, "a") if t is not None else None
            href = dom.attr(link, "href")
            if href and not href.startswith("http"):
                href = BASE + href
            if href and href in urls:
                continue
            if href and not href.startswith("http"):
                href = BASE + href
            # Si la desc del card también es basura, entrar al artículo
            if _is_junk(desc):
                if href:
                    desc = _fetch_article_lead(href) or title
            seen.add(title)
            if href:
                urls.add(href)
            items.append({"titulo": title, "descripcion": desc, "url": href})

    return items[:limit]


def main():
    try:
        r = requests.get(START_URL, headers=HDRS, timeout=TIMEOUT)
        if r.status_code != 200:
            log("HTTP", r.status_code)
            _atomic_write_json(OUT_PATH, [])
            print(f"ERROR: HTTP {r.status_code} en {START_URL}")
            sys.exit(1)
        items = _extract(r.text, limit=12)
        log("items extraídos:", len(items))
        _atomic_write_json(OUT_PATH, items or [])
        if not items:
            print(f"AVISO: 0 items en {OUT_PATH}")
            sys.exit(1)
        print(f"OK: Guardado {len(items)} items en {OUT_PATH}")
    except Exception as e:
        try: _atomic_write_json(OUT_PATH, [])
        except Exception: pass
        print("ERROR:", e)
        if DEBUG: traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
