# core/image_fetcher.py
"""
Busca una imagen relevante en Unsplash usando los tags generados por Groq.

Flujo:
  1. Recibe los tags del artículo reescrito (ej: ["transporte", "ciudad", "Buenos Aires"])
  2. Hace una búsqueda en Unsplash con los primeros 2-3 tags
  3. Descarga la imagen al directorio de uploads
  4. Devuelve el path local para que cron_base la suba a WordPress

Unsplash API:
  - Endpoint: GET /search/photos
  - Requiere: UNSPLASH_ACCESS_KEY en el entorno
  - Límite free: 50 requests/hora — más que suficiente para el cron
  - Licencia: todas las fotos son libres para uso comercial y editorial

Documentación: https://unsplash.com/documentation#search-photos
"""
from __future__ import annotations

import os
import time
import pathlib
import logging
import requests
from typing import Optional, List

UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")
UNSPLASH_API_URL    = "https://api.unsplash.com/search/photos"
UNSPLASH_UTM        = "utm_source=notibot&utm_medium=referral"  # requerido por TOS

log = logging.getLogger("notibot.image_fetcher")


def _build_query(tags: List[str]) -> str:
    """
    Construye la query para Unsplash a partir de los tags.
    Usa los primeros 3, priorizando los más específicos (más cortos y concretos).
    Siempre agrega "Buenos Aires" para que las fotos sean relevantes geográficamente.
    """
    clean = [t.strip() for t in (tags or []) if t.strip()]

    # Sacar tags genéricos que dan resultados malos en Unsplash
    skip = {"argentina", "noticias", "ciudad", "error", "ba", "gcba", "gobierno"}
    filtered = [t for t in clean if t.lower() not in skip]

    # Tomar los primeros 2 tags específicos + Buenos Aires
    selected = filtered[:2]
    if "Buenos Aires" not in selected:
        selected.append("Buenos Aires")

    query = " ".join(selected)
    log.debug("Query Unsplash: %r", query)
    return query


def fetch_image(
    tags: List[str],
    save_dir: pathlib.Path,
    orientation: str = "landscape",
) -> Optional[pathlib.Path]:
    """
    Busca una foto en Unsplash y la descarga en save_dir.

    Args:
        tags:        lista de tags del artículo (viene del JSON de Groq)
        save_dir:    directorio donde guardar la imagen descargada
        orientation: "landscape" | "portrait" | "squarish"

    Returns:
        Path al archivo descargado, o None si falló.
    """
    if not UNSPLASH_ACCESS_KEY:
        log.warning("UNSPLASH_ACCESS_KEY no configurada, sin imagen.")
        return None

    query = _build_query(tags)
    if not query:
        log.warning("No hay tags útiles para buscar imagen.")
        return None

    # 1. Buscar en Unsplash
    try:
        resp = requests.get(
            UNSPLASH_API_URL,
            params={
                "query":       query,
                "orientation": orientation,
                "per_page":    10,
                "lang":        "es",
            },
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            timeout=15,
        )
        if resp.status_code != 200:
            log.error("Unsplash API error %s: %s", resp.status_code, resp.text[:200])
            return None

        results = resp.json().get("results", [])
        if not results:
            # Fallback: buscar solo "Buenos Aires ciudad"
            log.info("Sin resultados para %r, fallback a 'Buenos Aires ciudad'", query)
            resp2 = requests.get(
                UNSPLASH_API_URL,
                params={"query": "Buenos Aires ciudad", "orientation": orientation, "per_page": 5},
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=15,
            )
            results = resp2.json().get("results", []) if resp2.status_code == 200 else []

        if not results:
            log.warning("Sin resultados en Unsplash para query: %r", query)
            return None

    except Exception as e:
        log.error("Error buscando en Unsplash: %s", e)
        return None

    # 2. Elegir la foto con mayor resolución disponible
    photo = results[0]
    download_url = (
        photo.get("urls", {}).get("regular") or
        photo.get("urls", {}).get("small")
    )
    if not download_url:
        log.warning("Foto sin URL descargable")
        return None

    photo_id   = photo.get("id", "unknown")
    author     = photo.get("user", {}).get("name", "unknown")
    author_url = photo.get("user", {}).get("links", {}).get("html", "")
    log.info("Imagen elegida: id=%s autor=%s", photo_id, author)

    # 3. Registrar el download (requerido por Unsplash TOS para estadísticas)
    try:
        dl_url = photo.get("links", {}).get("download_location", "")
        if dl_url:
            requests.get(
                dl_url,
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                timeout=10,
            )
    except Exception:
        pass  # no crítico

    # 4. Descargar la imagen
    save_dir.mkdir(parents=True, exist_ok=True)
    filename = f"unsplash_{photo_id}_{int(time.time())}.jpg"
    dest = save_dir / filename

    try:
        img_resp = requests.get(download_url, timeout=30, stream=True)
        if img_resp.status_code != 200:
            log.error("No pude descargar imagen: HTTP %s", img_resp.status_code)
            return None
        with open(dest, "wb") as f:
            for chunk in img_resp.iter_content(chunk_size=8192):
                f.write(chunk)
        log.info("Imagen descargada: %s", dest)
    except Exception as e:
        log.error("Error descargando imagen: %s", e)
        return None

    # 5. Guardar crédito (útil para debug / atribución en el pie del artículo si querés)
    credit_file = save_dir / f"unsplash_{photo_id}_credit.txt"
    try:
        credit_file.write_text(
            f"Photo by {author} ({author_url}?{UNSPLASH_UTM}) on Unsplash\n"
            f"Query: {query}\n"
            f"Photo URL: https://unsplash.com/photos/{photo_id}?{UNSPLASH_UTM}\n",
            encoding="utf-8",
        )
    except Exception:
        pass

    return dest
