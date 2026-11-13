# core/wp_prepare.py
"""
Preparación de un WordPress local para que acepte las publicaciones del notibot.

Hace lo que antes se hacía a mano en el servidor:
  1. verifica que esté el plugin JWT Authentication for WP REST API,
  2. define JWT_AUTH_SECRET_KEY en wp-config.php (con backup, sin duplicar),
  3. agrega las reglas de Authorization al .htaccess (Apache),
  4. opcionalmente instala el plugin desde el repositorio oficial de WordPress.

Pensado para sitios propios (carpetas `sitioN` del servidor). Para hosting
externo devuelve un checklist, no toca nada.
"""
import os
import pathlib
import re
import secrets
import shutil
import time
import urllib.request
import zipfile
from typing import Any, Dict, List, Optional

PLUGIN_SLUG = "jwt-authentication-for-wp-rest-api"
PLUGIN_ZIP = "https://downloads.wordpress.org/plugin/%s.latest-stable.zip" % PLUGIN_SLUG

MARCA_CONFIG = "/* That's all, stop editing!"
MARCA_CONFIG_ALT = "/* Stop editing"
FINAL_CONFIG = "/* That's all, stop editing!"

HTACCESS_BLOQUE = """
# BEGIN Notibot JWT
<IfModule mod_rewrite.c>
RewriteEngine on
RewriteCond %{{HTTP:Authorization}} ^(.*)
RewriteRule ^(.*) - [E=HTTP_AUTHORIZATION:%1]
</IfModule>
SetEnvIf Authorization "(.*)" HTTP_AUTHORIZATION=$1
# END Notibot JWT
"""


# ─── Resolución de rutas ────────────────────────────────────────────────────

def resolver_ruta(ruta: str) -> Optional[pathlib.Path]:
    """
    Encuentra la carpeta del sitio. Si el panel corre en un container, permite
    mapear la ruta del host con NOTIBOT_SITES_HOST / NOTIBOT_SITES_DIR.
    """
    if not ruta:
        return None
    p = pathlib.Path(ruta).expanduser()
    if p.exists():
        return p.resolve()

    base = (os.getenv("NOTIBOT_SITES_DIR") or "").strip()
    host = (os.getenv("NOTIBOT_SITES_HOST") or "").strip().rstrip("/")
    if base:
        b = pathlib.Path(base)
        if host and ruta.startswith(host):
            cand = b / ruta[len(host):].lstrip("/")
            if cand.exists():
                return cand.resolve()
        cand = b / p.name
        if cand.exists():
            return cand.resolve()
    return None


def localizar_wp_config(ruta: str) -> Optional[pathlib.Path]:
    """Devuelve el wp-config.php del sitio, buscando en las ubicaciones típicas."""
    p = resolver_ruta(ruta)
    if not p:
        return None
    if p.is_file() and p.name == "wp-config.php":
        return p
    candidatos = [p / "wp-config.php", p / "wordpress" / "wp-config.php",
                  p / "public" / "wp-config.php", p / "html" / "wp-config.php",
                  p / "public_html" / "wp-config.php"]
    for c in candidatos:
        if c.exists():
            return c
    for c in sorted(p.glob("*/wp-config.php")):
        return c
    return None


def carpeta_wp(wp_config: pathlib.Path) -> pathlib.Path:
    return wp_config.parent


# ─── Estado ─────────────────────────────────────────────────────────────────

def _leer(path: pathlib.Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _constante_definida(texto: str) -> bool:
    return bool(re.search(r"JWT_AUTH_SECRET_KEY", texto or ""))


def _htaccess_ok(texto: str) -> bool:
    return "HTTP_AUTHORIZATION" in (texto or "") or "HTTP:Authorization" in (texto or "")


def estado(ruta: str) -> Dict[str, Any]:
    wp_config = localizar_wp_config(ruta)
    if not wp_config:
        return {"ok": False, "error": "No encontré wp-config.php en esa carpeta.",
                "ruta": ruta, "local": False}
    raiz = carpeta_wp(wp_config)
    plugin_dir = raiz / "wp-content" / "plugins" / PLUGIN_SLUG
    htaccess = raiz / ".htaccess"
    texto_config = _leer(wp_config)
    return {
        "ok": True,
        "ruta": str(raiz),
        "wp_config": str(wp_config),
        "plugin": plugin_dir.exists(),
        "plugin_dir": str(plugin_dir),
        "constante": _constante_definida(texto_config),
        "htaccess": htaccess.exists(),
        "htaccess_ok": _htaccess_ok(_leer(htaccess)),
        "htaccess_path": str(htaccess),
        "es_local": True,
    }


# ─── Acciones ───────────────────────────────────────────────────────────────

def _backup(path: pathlib.Path) -> Optional[pathlib.Path]:
    destino = path.with_name("%s.bak-notibot-%s" % (path.name, time.strftime("%Y%m%d-%H%M%S")))
    try:
        shutil.copy2(str(path), str(destino))
        return destino
    except Exception:
        return None


def _insertar_constante(wp_config: pathlib.Path, clave: str) -> Dict[str, Any]:
    texto = _leer(wp_config)
    if _constante_definida(texto):
        return {"ok": True, "detalle": "JWT_AUTH_SECRET_KEY ya estaba definida; no la toco."}
    linea = "\n/* Notibot: clave del plugin JWT Authentication */\ndefine('JWT_AUTH_SECRET_KEY', '%s');\n\n" % clave

    if MARCA_CONFIG in texto:
        nuevo = texto.replace(MARCA_CONFIG, linea + MARCA_CONFIG, 1)
    elif MARCA_CONFIG_ALT in texto:
        nuevo = texto.replace(MARCA_CONFIG_ALT, linea + MARCA_CONFIG_ALT, 1)
    else:
        nuevo = texto.rstrip() + "\n" + linea

    respaldo = _backup(wp_config)
    if not respaldo:
        return {"ok": False, "detalle": "No pude hacer backup de wp-config.php; no lo modifico."}
    try:
        # escribir sobre el mismo archivo para conservar dueño y permisos
        with open(str(wp_config), "w", encoding="utf-8") as f:
            f.write(nuevo)
    except Exception as e:
        return {"ok": False, "detalle": "No pude escribir wp-config.php: %s" % str(e)[:120]}
    return {"ok": True, "detalle": "Clave agregada a wp-config.php.", "backup": respaldo.name}


def _arreglar_htaccess(raiz: pathlib.Path) -> Dict[str, Any]:
    htaccess = raiz / ".htaccess"
    if not htaccess.exists():
        return {"ok": True, "detalle": "No hay .htaccess (si es Nginx, la regla no hace falta)."}
    texto = _leer(htaccess)
    if _htaccess_ok(texto):
        return {"ok": True, "detalle": "El .htaccess ya expone el header Authorization."}
    respaldo = _backup(htaccess)
    if not respaldo:
        return {"ok": False, "detalle": "No pude hacer backup del .htaccess."}
    try:
        with open(str(htaccess), "a", encoding="utf-8") as f:
            f.write(HTACCESS_BLOQUE)
    except Exception as e:
        return {"ok": False, "detalle": "No pude escribir el .htaccess: %s" % str(e)[:120]}
    return {"ok": True, "detalle": "Reglas de Authorization agregadas al .htaccess.", "backup": respaldo.name}


def _instalar_plugin(raiz: pathlib.Path) -> Dict[str, Any]:
    destino = raiz / "wp-content" / "plugins"
    if (destino / PLUGIN_SLUG).exists():
        return {"ok": True, "detalle": "El plugin ya está instalado."}
    tmp = raiz / (".notibot_%s.zip" % PLUGIN_SLUG)
    try:
        destino.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(PLUGIN_ZIP, str(tmp))
        base = destino.resolve()
        with zipfile.ZipFile(str(tmp)) as z:
            for miembro in z.namelist():
                # evitar zip-slip: nada puede quedar fuera de wp-content/plugins
                objetivo = (base / miembro).resolve()
                if base != objetivo and base not in objetivo.parents:
                    raise RuntimeError("zip con rutas inseguras: %s" % miembro)
            z.extractall(str(base))
        return {"ok": True, "detalle": "Plugin JWT descargado e instalado (queda activarlo en WP)."}
    except Exception as e:
        return {"ok": False, "detalle": "No pude instalar el plugin: %s" % str(e)[:140]}
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def preparar(ruta: str, dry_run: bool = False, instalar_plugin: bool = False) -> Dict[str, Any]:
    """
    Prepara el sitio. Devuelve {ok, ruta, pasos: [{accion, ok, detalle}], errores: []}.
    Con dry_run no escribe nada.
    """
    est = estado(ruta)
    if not est.get("ok"):
        return {"ok": False, "errores": [est.get("error", "No encontré el sitio.")], "pasos": [], "estado": est}

    raiz = pathlib.Path(est["ruta"])
    wp_config = pathlib.Path(est["wp_config"])
    pasos: List[Dict[str, Any]] = []

    if instalar_plugin and not est["plugin"]:
        if dry_run:
            pasos.append({"accion": "Instalar plugin JWT", "ok": True, "detalle": "[dry-run] se descargaría de wordpress.org"})
        else:
            p = _instalar_plugin(raiz)
            pasos.append({"accion": "Instalar plugin JWT", "ok": p["ok"], "detalle": p["detalle"]})
    elif not est["plugin"]:
        pasos.append({"accion": "Plugin JWT",
                      "ok": False,
                      "detalle": "No está instalado en wp-content/plugins/%s. Instalalo desde WP o marcá "
                                 "'instalar el plugin si falta'." % PLUGIN_SLUG})
        return {"ok": False, "errores": ["Falta el plugin JWT Authentication."], "pasos": pasos, "estado": est}

    if est["constante"]:
        pasos.append({"accion": "Clave JWT", "ok": True, "detalle": "JWT_AUTH_SECRET_KEY ya estaba definida."})
    elif dry_run:
        pasos.append({"accion": "Clave JWT", "ok": True,
                      "detalle": "[dry-run] se agregaría JWT_AUTH_SECRET_KEY a wp-config.php"})
    else:
        p = _insertar_constante(wp_config, secrets.token_urlsafe(48))
        pasos.append({"accion": "Clave JWT", "ok": p["ok"], "detalle": p["detalle"]})

    if dry_run:
        pasos.append({"accion": ".htaccess", "ok": True,
                      "detalle": "[dry-run] se revisarían las reglas de Authorization"})
    else:
        pasos.append(dict(accion=".htaccess", **_arreglar_htaccess(raiz)))

    errores = [p["detalle"] for p in pasos if not p["ok"]]
    return {"ok": not errores, "errores": errores, "pasos": pasos, "estado": estado(ruta), "dry_run": dry_run}


def checklist() -> str:
    """Texto para sitios en hosting externo (sin acceso a archivos)."""
    return (
        "En un hosting externo hay que hacerlo desde el panel de WordPress:\n"
        "1. Instalar y activar el plugin 'JWT Authentication for WP REST API'.\n"
        "2. Agregar en wp-config.php: define('JWT_AUTH_SECRET_KEY', '<clave larga y aleatoria>');\n"
        "3. Si el servidor es Apache, permitir el header Authorization en .htaccess.\n"
        "4. Verificar /wp-json/jwt-auth/v1/token con el usuario y la clave del bot."
    )
