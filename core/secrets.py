# core/secrets.py
"""
Carga de credenciales de diarios desde un archivo de secretos.

Los diarios que se dan de alta desde el panel no pueden sumar variables al
`variables.env` del contenedor (eso exigiría recrear el container). El panel
escribe las credenciales en `config/secrets.env` (permisos 600) y este módulo
las inyecta en `os.environ` al arrancar cualquier proceso que importe
`core.diarios` (workers, bot y panel).

Formato: una línea `NOMBRE=valor` por variable, con `#` para comentarios.
Nunca se loguea el contenido de este archivo.
"""
import os
import pathlib
from typing import Optional


def default_path() -> pathlib.Path:
    env = (os.getenv("NOTIBOT_SECRETS_ENV") or "").strip()
    if env:
        return pathlib.Path(env)
    return pathlib.Path(__file__).resolve().parent.parent / "config" / "secrets.env"


def load_secrets(path: Optional[pathlib.Path] = None, override: bool = False) -> int:
    """Inyecta las variables del archivo de secretos. Devuelve cuántas cargó."""
    p = pathlib.Path(path) if path else default_path()
    if not p.exists():
        return 0
    cargadas = 0
    try:
        contenido = p.read_text(encoding="utf-8")
    except Exception:
        return 0
    for linea in contenido.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if linea.startswith("export "):
            linea = linea[len("export "):].strip()
        if "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        clave = clave.strip()
        valor = valor.strip().strip('"').strip("'")
        if not clave:
            continue
        if override or not (os.environ.get(clave) or "").strip():
            os.environ[clave] = valor
            cargadas += 1
    return cargadas
