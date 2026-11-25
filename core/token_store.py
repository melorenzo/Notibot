import os
import json
import pathlib
from datetime import datetime, timedelta
from typing import Optional
import requests

# El código vive en core/ y el estado sigue en token_jwt/tokens.json (esa carpeta
# no va al repo). La imagen fija TOKENS_PATH=/app/token_jwt/tokens.json.
_ROOT = pathlib.Path(os.getenv("APP_ROOT") or pathlib.Path(__file__).resolve().parent.parent)
TOKENS_PATH = os.getenv("TOKENS_PATH") or str(_ROOT / "token_jwt" / "tokens.json")

def _load_tokens():
    if not os.path.exists(TOKENS_PATH):
        return {}
    with open(TOKENS_PATH, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _save_tokens(data):
    os.makedirs(os.path.dirname(TOKENS_PATH), exist_ok=True)
    with open(TOKENS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _is_valid(expires_at: str, skew_seconds: int = 300) -> bool:
    try:
        dt = datetime.fromisoformat(expires_at)
        return dt > datetime.now() + timedelta(seconds=skew_seconds)
    except Exception:
        return False

def get_token_for_diario(diario_cfg: dict, force_refresh: bool = False) -> Optional[str]:
    """Return a valid JWT for given diario. Refresh if needed. Credentials come from env."""
    tokens = _load_tokens()
    slug = None
    # try to guess slug from base_url to index tokens file
    # fallback: use name
    for key, val in tokens.items():
        if isinstance(val, dict) and val.get("base_url") == diario_cfg.get("base_url"):
            slug = key
            break
    if slug is None:
        slug = diario_cfg.get("name", "unknown").lower().replace(" ", "_")

    # Existing token still valid?
    if not force_refresh and slug in tokens and _is_valid(tokens[slug].get("expires_at", "")):
        return tokens[slug]["token"]

    # Otherwise refresh
    username = os.getenv(diario_cfg.get("username_env") or "", "")
    password = os.getenv(diario_cfg.get("password_env") or "", "")
    if not username or not password:
        raise RuntimeError(f"Faltan credenciales en variables de entorno para {diario_cfg.get('name')}")

    token_ep = diario_cfg["base_url"].rstrip("/") + diario_cfg["jwt_token_url"]
    resp = requests.post(token_ep, json={"username": username, "password": password}, headers={"Content-Type":"application/json"})
    if resp.status_code != 200:
        raise RuntimeError(f"No pude obtener el token JWT ({resp.status_code}): {resp.text}")

    token = resp.json().get("token")
    if not token:
        raise RuntimeError("La respuesta JWT no trae 'token'")

    tokens[slug] = {
        "token": token,
        "base_url": diario_cfg.get("base_url"),
        "expires_at": (datetime.now() + timedelta(days=7)).isoformat()
    }
    _save_tokens(tokens)
    return token
