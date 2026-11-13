# core/dedup.py
"""
Deduplicación de noticias entre crons y bot.

Antes el hash era solo sha256(título + descripción). Si el portal reescribía
la bajada de una nota ya publicada, el hash cambiaba y la nota se publicaba
otra vez. Ahora se calcula el hash por URL y por título normalizado, y se
guardan los tres (el legacy se mantiene por compatibilidad con los
used_items.json ya existentes).
"""
import hashlib
import re
from typing import Any, Dict, List, Tuple


def hash_item(titulo: str, desc: str) -> str:
    """Hash histórico (título + descripción)."""
    h = hashlib.sha256()
    h.update((titulo or "").strip().encode())
    h.update(b"||")
    h.update((desc or "").strip().encode())
    return h.hexdigest()


def norm_title(titulo: str) -> str:
    return re.sub(r"\s+", " ", (titulo or "")).strip().lower()


def item_hashes(item: Dict[str, Any]) -> Tuple[str, List[str]]:
    """Devuelve (hash_legacy, [hash_url, hash_titulo])."""
    titulo = (item.get("titulo") or "").strip()
    desc = (item.get("descripcion") or "").strip()
    url = (item.get("url") or "").strip().split("?")[0].rstrip("/")

    legacy = hash_item(titulo, desc)
    hash_titulo = hashlib.sha256(norm_title(titulo).encode()).hexdigest()
    hash_url = hashlib.sha256(url.encode()).hexdigest() if url else hash_titulo
    return legacy, [hash_url, hash_titulo]


def is_used(item: Dict[str, Any], used: Dict[str, Any]) -> bool:
    legacy, extra = item_hashes(item)
    if legacy in used:
        return True
    return any(h in used for h in extra)
