import json
import os
from typing import Dict, Any, List

try:  # credenciales de diarios dados de alta desde el panel
    from core.secrets import load_secrets as _load_secrets
except Exception:  # pragma: no cover - import como script suelto
    try:
        from secrets import load_secrets as _load_secrets  # type: ignore
    except Exception:
        _load_secrets = None  # type: ignore

if _load_secrets:
    _load_secrets()

CONFIG_PATH = os.getenv("DIARIOS_CONFIG_PATH", os.path.join(os.path.dirname(__file__), "..", "config", "diarios.json"))


def _load() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def all_diarios() -> Dict[str, Any]:
    return _load()


def get_diario(slug: str) -> Dict[str, Any]:
    return _load().get(slug)


def diarios_for_user(telegram_id: int) -> Dict[str, Any]:
    cfg = _load()
    out = {}
    for slug, data in cfg.items():
        ids = data.get("allowed_telegram_ids") or []
        if not ids or telegram_id in ids:
            out[slug] = data
    return out


def esta_activo(diario: Dict[str, Any]) -> bool:
    return bool(diario.get("activo", True))


def base_url(diario: Dict[str, Any]) -> str:
    url = diario["base_url"].rstrip("/")
    return url


def posts_url(diario: Dict[str, Any]) -> str:
    return base_url(diario) + diario["wp_posts_endpoint"]


def media_url(diario: Dict[str, Any]) -> str:
    return base_url(diario) + diario["wp_media_endpoint"]


def token_url(diario: Dict[str, Any]) -> str:
    return base_url(diario) + diario["jwt_token_url"]
