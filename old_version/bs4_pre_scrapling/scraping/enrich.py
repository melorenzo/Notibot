# scraping/enrich.py
"""
Enriquecimiento de un item de noticia a partir de su URL.

El feed RSS trae un copete corto (~200 chars). Para que la IA (Groq) pueda
escribir una nota con contexto, y para tener imagen de portada, conviene
entrar a la nota original y extraer:

  - imagen de portada (og:image / twitter:image / primer <img>)
  - los primeros párrafos del cuerpo

Se aplica SOLO al item que se va a publicar (1 request por publicación),
no a los 30 del feed.
"""
import html as html_lib
import re
from typing import Any, Dict

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/124.0 Safari/537.36"),
    "Accept-Language": "es-AR,es;q=0.9",
}

_JUNK = re.compile(
    r"(cookies?|newsletter|suscrib|compartí|compartir|©|todos los derechos|"
    r"leer también|te interesa|seguir leyendo|fotograf[ií]as ilustrativas|"
    r"pexels|pixabay|licencia cc|contacto|aviso legal)",
    re.I,
)

# Ticker del sitio (clima, dólar, subte) — no es parte de la nota.
_TICKER = re.compile(
    r"(d[óo]lar oficial|estado del subte|blue a\s*\$|cotiza|"
    r"parcialmente nublado|despejado|d[óo]lar blue)",
    re.I,
)


def _clean(s: str) -> str:
    s = html_lib.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return " ".join(s.split()).strip()


def _meta(soup, *keys: str) -> str:
    for key in keys:
        tag = (soup.find("meta", attrs={"property": key})
               or soup.find("meta", attrs={"name": key}))
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def _paragraphs(soup) -> list:
    out = []
    for p in soup.find_all("p"):
        t = _clean(p.get_text(" ", strip=True))
        if len(t) < 90:
            continue
        if _JUNK.search(t) or _TICKER.search(t):
            continue
        if t.count("·") >= 2:
            continue
        out.append(t)
    return out


def _image(soup) -> str:
    url = _meta(soup, "og:image", "twitter:image", "twitter:image:src")
    if url:
        return url
    img = soup.find("img")
    if img:
        return (img.get("src") or img.get("data-src") or "").strip()
    return ""


def enrich_item(item: Dict[str, Any], timeout: int = 20) -> Dict[str, Any]:
    """
    Devuelve una copia del item con "imagen_url" y "descripcion" mejorados.
    Si algo falla, devuelve el item original (nunca rompe el pipeline).
    """
    url = (item.get("url") or "").strip()
    if not url.startswith("http"):
        return item

    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        if r.status_code != 200:
            return item
        # requests decodifica como ISO-8859-1 cuando el servidor no declara
        # charset, y los sitios porteños mandan UTF-8 → texto con "Ã¡".
        if not r.encoding or r.encoding.lower() in ("iso-8859-1", "latin-1"):
            r.encoding = r.apparent_encoding or "utf-8"

        soup = BeautifulSoup(r.text, "html.parser")

        enriched = dict(item)

        img = _image(soup)
        if img and not (item.get("imagen_url") or "").strip():
            enriched["imagen_url"] = img

        desc_actual = (item.get("descripcion") or "").strip()
        body = " ".join(_paragraphs(soup)[:3])
        if len(body) > len(desc_actual):
            enriched["descripcion"] = body[:1500]

        return enriched
    except Exception:
        return item
