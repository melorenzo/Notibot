# panel/wp_client.py
"""
Cliente mínimo de WordPress para el panel: probar el login JWT antes de dar
de alta un diario, leer estado público del sitio y publicar una nota de prueba.

Los mensajes de error están pensados para que se entienda qué falta del lado
del WordPress (plugin JWT, clave secreta, Cloudflare, usuario/clave).
"""
from typing import Any, Dict, Optional

import requests

UA = {"User-Agent": "Notibot-Panel/1.0", "Accept": "application/json"}


def _url(base_url: str, path: str) -> str:
    return (base_url or "").rstrip("/") + path


def _resultado(ok: bool, estado: str, mensaje: str, **extra: Any) -> Dict[str, Any]:
    data = {"ok": ok, "estado": estado, "mensaje": mensaje}
    data.update(extra)
    return data


def _es_cloudflare(resp: requests.Response) -> bool:
    texto = (resp.text or "")[:4000].lower()
    server = (resp.headers.get("server") or "").lower()
    return (
        resp.headers.get("cf-mitigated") is not None
        or "cloudflare" in server
        or "just a moment" in texto
        or "attention required" in texto
        or "cf-error-details" in texto
    )


def probar_jwt(base_url: str, username: str, password: str, timeout: int = 25) -> Dict[str, Any]:
    """POST /wp-json/jwt-auth/v1/token y diagnóstico legible del resultado."""
    endpoint = _url(base_url, "/wp-json/jwt-auth/v1/token")
    try:
        resp = requests.post(endpoint, json={"username": username, "password": password},
                             headers=UA, timeout=timeout)
    except requests.exceptions.SSLError:
        return _resultado(False, "ssl", "El certificado HTTPS del sitio no es válido o no cierra el TLS.")
    except requests.exceptions.ConnectionError:
        return _resultado(False, "red", "No pude conectar con el sitio. Revisá el dominio y que WordPress responda.")
    except requests.exceptions.Timeout:
        return _resultado(False, "timeout", "El sitio tardó demasiado en responder (timeout de %ds)." % timeout)
    except Exception as e:  # pragma: no cover
        return _resultado(False, "error", "Error inesperado al probar el login: %s" % str(e)[:160])

    if resp.status_code == 200:
        try:
            token = (resp.json() or {}).get("token")
        except Exception:
            token = None
        if token:
            return _resultado(True, "ok", "Login JWT correcto: el sitio acepta publicaciones del notibot.",
                              token=token)
        return _resultado(False, "sin_token", "El endpoint respondió 200 pero no devolvió un token.")

    if _es_cloudflare(resp):
        return _resultado(
            False, "cloudflare",
            "Cloudflare está bloqueando las peticiones al dominio público. "
            "Si el sitio está en el mismo servidor, usá la URL interna (http://servicio).",
        )
    if resp.status_code == 404:
        return _resultado(
            False, "sin_plugin",
            "No existe /wp-json/jwt-auth/v1/token: falta el plugin "
            "'JWT Authentication for WP REST API'.",
        )

    codigo = None
    try:
        codigo = (resp.json() or {}).get("code")
    except Exception:
        codigo = None

    mensajes = {
        "incorrect_password": "Usuario o contraseña incorrectos.",
        "invalid_username": "El usuario de WordPress no existe.",
        "jwt_auth_bad_config": "El plugin JWT está activo pero falta la constante JWT_AUTH_SECRET_KEY en wp-config.php.",
        "jwt_auth_no_auth_header": "El servidor está descartando el header Authorization (revisá las reglas de .htaccess).",
        "rest_no_route": "La REST API no expone la ruta JWT (revisá permalinks y el plugin).",
        "jwt_auth_failed": "Credenciales rechazadas por el plugin JWT.",
    }
    if codigo in mensajes:
        return _resultado(False, codigo, mensajes[codigo], http=resp.status_code, code=codigo)

    if resp.status_code in (401, 403):
        return _resultado(False, "rechazado", "El sitio rechazó el login (HTTP %d)." % resp.status_code,
                          http=resp.status_code)
    return _resultado(False, "http", "Respuesta inesperada HTTP %d: %s" % (resp.status_code, (resp.text or "")[:160]),
                      http=resp.status_code)


def info_sitio(base_url: str, timeout: int = 6) -> Dict[str, Any]:
    """GET /wp-json/ — nombre y descripción del sitio (público, sin credenciales)."""
    try:
        resp = requests.get(_url(base_url, "/wp-json/"), headers=UA, timeout=timeout)
        if resp.status_code != 200:
            return {"ok": False, "nombre": None, "descripcion": None}
        data = resp.json() or {}
        return {
            "ok": True,
            "nombre": data.get("name"),
            "descripcion": data.get("description"),
            "home": data.get("home") or data.get("url"),
        }
    except Exception:
        return {"ok": False, "nombre": None, "descripcion": None}


def crear_borrador(diario_cfg: Dict[str, Any], token: str, timeout: int = 40) -> Dict[str, Any]:
    """Publica una nota de prueba como borrador para verificar el permiso de escritura."""
    url = diarios_posts_url(diario_cfg)
    payload = {
        "title": "Nota de prueba — Notibot",
        "content": (
            "<p>Esta nota la generó el panel del Notibot para verificar que el diario "
            "acepta publicaciones automáticas.</p>\n"
            "<p>Se puede borrar sin problema.</p>"
        ),
        "status": "draft",
    }
    try:
        resp = requests.post(url, json=payload, headers={
            "Authorization": "Bearer %s" % token,
            "Content-Type": "application/json",
            "User-Agent": UA["User-Agent"],
        }, timeout=timeout)
    except Exception as e:
        return {"ok": False, "mensaje": "No pude crear el borrador: %s" % str(e)[:160]}
    if resp.status_code in (200, 201):
        data = resp.json() or {}
        return {"ok": True, "id": data.get("id"), "link": data.get("link"),
                "mensaje": "Borrador de prueba creado (ID %s)." % data.get("id")}
    return {"ok": False, "mensaje": "WordPress respondió %d: %s" % (resp.status_code, (resp.text or "")[:200])}


def diarios_posts_url(diario_cfg: Dict[str, Any]) -> str:
    return _url(diario_cfg.get("base_url") or "", diario_cfg.get("wp_posts_endpoint") or "/wp-json/wp/v2/posts")
