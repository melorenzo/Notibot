# panel/app.py
"""
Panel web del Notibot.

    PANEL_PASSWORD=... uvicorn panel.app:app --host 0.0.0.0 --port 8030

Sin PANEL_PASSWORD el panel no permite ingresar. Variables opcionales:
  PANEL_SECRET=...         secreto extra para firmar la cookie de sesión
  PANEL_COOKIE_SECURE=1    fuerza la cookie Secure (si no, se detecta por HTTPS)
  PANEL_TRUST_PROXY=1      honra X-Forwarded-For para el límite de intentos
  PANEL_SCHEDULER=0        apaga el programador interno
"""
import hashlib
import hmac
import os
import secrets as pysecrets
import time
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from core import wp_prepare
from panel import config_store, runner, stats, wp_client

ROOT = config_store.app_root()
COOKIE = "notibot_panel"
TTL_SESION = 12 * 3600
MAX_INTENTOS = 6
BLOQUEO = 300

CABECERAS_SEGURIDAD = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    runner.iniciar_scheduler()
    if not os.getenv("PANEL_PASSWORD"):
        print("[panel] ATENCIÓN: falta PANEL_PASSWORD; nadie va a poder ingresar al panel.", flush=True)
    yield
    runner.detener()


app = FastAPI(title="Notibot — Panel", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(ROOT / "panel" / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "panel" / "templates"))
templates.env.filters["hace"] = stats.tiempo_relativo
templates.env.filters["fecha"] = stats.fecha_corta


@app.middleware("http")
async def cabeceras(request: Request, call_next):
    respuesta = await call_next(request)
    for clave, valor in CABECERAS_SEGURIDAD.items():
        respuesta.headers.setdefault(clave, valor)
    if "text/html" in respuesta.headers.get("content-type", ""):
        respuesta.headers["Cache-Control"] = "no-store"
    return respuesta


# ─── Sesión ─────────────────────────────────────────────────────────────────

def _secreto() -> str:
    base = (os.getenv("PANEL_SECRET") or "") + "|" + (os.getenv("PANEL_PASSWORD") or "")
    return hashlib.sha256(base.encode()).hexdigest()


def _firmar(payload: str) -> str:
    return hmac.new(_secreto().encode(), payload.encode(), hashlib.sha256).hexdigest()


def crear_sesion() -> str:
    payload = "%d.%s" % (int(time.time()) + TTL_SESION, pysecrets.token_hex(16))
    return "%s.%s" % (payload, _firmar(payload))


def leer_sesion(valor: Optional[str]) -> Optional[str]:
    """Devuelve el nonce (que también hace de token CSRF) si la cookie es válida."""
    if not valor:
        return None
    partes = valor.split(".")
    if len(partes) != 3:
        return None
    exp, nonce, firma = partes
    if not hmac.compare_digest(firma, _firmar("%s.%s" % (exp, nonce))):
        return None
    try:
        if int(exp) < time.time():
            return None
    except ValueError:
        return None
    return nonce


def _ip(request: Request) -> str:
    if os.getenv("PANEL_TRUST_PROXY", "0") == "1":
        reenviada = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
        if reenviada:
            return reenviada[:64]
    return (request.client.host if request.client else "?")[:64]


_intentos: Dict[str, List[float]] = {}


def _bloqueado(ip: str) -> bool:
    ahora = time.time()
    marcas = [t for t in _intentos.get(ip, []) if ahora - t < BLOQUEO]
    _intentos[ip] = marcas
    return len(marcas) >= MAX_INTENTOS


def _anotar_fallo(ip: str) -> None:
    marcas = _intentos.setdefault(ip, [])
    marcas.append(time.time())
    if len(_intentos) > 2000:  # techo de memoria
        for clave in [k for k, v in _intentos.items() if not v][:500]:
            _intentos.pop(clave, None)


def _csrf_ok(request: Request, token: str) -> bool:
    ses = leer_sesion(request.cookies.get(COOKIE))
    return bool(ses and token and hmac.compare_digest(ses, token))


def _cookie_segura(request: Request) -> bool:
    if os.getenv("PANEL_COOKIE_SECURE", "0") == "1":
        return True
    if request.url.scheme == "https":
        return True
    return (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip() == "https"


# ─── Render ─────────────────────────────────────────────────────────────────

def _render(request: Request, plantilla: str, ses: Optional[str], seccion: str,
            codigo: int = 200, **extra: Any) -> HTMLResponse:
    contexto: Dict[str, Any] = {
        "request": request,
        "csrf": ses or "",
        "seccion": seccion,
        "scheduler_activo": runner.scheduler_activo(),
        "ok": request.query_params.get("ok", ""),
        "err": request.query_params.get("err", ""),
    }
    contexto.update(extra)
    return templates.TemplateResponse(name=plantilla, request=request, context=contexto, status_code=codigo)


def _volver(url: str, ok: str = "", err: str = "") -> RedirectResponse:
    destino = url
    if ok:
        destino += ("&" if "?" in url else "?") + "ok=" + quote(ok)
    if err:
        destino += ("&" if "?" in url else "?") + "err=" + quote(err)
    return RedirectResponse(destino, status_code=303)


def _valores_form(nombre: str, slug: str, base_url: str, usuario: str, dueno: str,
                  telegram_id: str, horarios: str, notas: int, tonos: List[str],
                  sitio_dir: str, preparar_wp: bool, instalar_plugin: bool,
                  forzar: bool, borrador: bool) -> Dict[str, Any]:
    return {
        "nombre": nombre, "slug": slug, "base_url": base_url, "usuario": usuario,
        "dueno": dueno, "telegram_id": telegram_id, "horarios": horarios,
        "notas_por_corrida": notas, "tonos": tonos, "sitio_dir": sitio_dir,
        "preparar_wp": preparar_wp, "instalar_plugin": instalar_plugin,
        "forzar": forzar, "borrador": borrador,
    }


# ─── Login / logout ─────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request, err: str = ""):
    if leer_sesion(request.cookies.get(COOKIE)):
        return RedirectResponse("/", status_code=303)
    return _render(request, "login.html", None, "login", err=err)


@app.post("/login")
def login(request: Request, password: str = Form("")):
    ip = _ip(request)
    esperado = os.getenv("PANEL_PASSWORD") or ""
    if not esperado:
        return _volver("/login", err="El panel no tiene PANEL_PASSWORD configurada.")
    if _bloqueado(ip):
        return _volver("/login", err="Demasiados intentos. Esperá 5 minutos.")
    if not hmac.compare_digest(password.encode(), esperado.encode()):
        _anotar_fallo(ip)
        return _volver("/login", err="Contraseña incorrecta.")
    _intentos.pop(ip, None)
    respuesta = RedirectResponse("/", status_code=303)
    respuesta.set_cookie(COOKIE, crear_sesion(), httponly=True, samesite="lax",
                         secure=_cookie_segura(request), max_age=TTL_SESION, path="/")
    return respuesta


@app.post("/logout")
def logout(request: Request):
    respuesta = RedirectResponse("/login", status_code=303)
    respuesta.delete_cookie(COOKIE, path="/")
    return respuesta


# ─── Tablero ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def tablero(request: Request):
    ses = leer_sesion(request.cookies.get(COOKIE))
    if not ses:
        return RedirectResponse("/login", status_code=303)
    error = ""
    try:
        datos = stats.construir_tablero()
    except Exception as e:
        datos = {"filas": [], "recientes": [],
                 "resumen": {"total": 0, "activos": 0, "notas_hoy": 0, "notas_7d": 0,
                             "problemas": 0, "en_panel": 0, "capturado": "—"}}
        error = "No pude leer las estadísticas: %s" % str(e)[:180]
    return _render(request, "dashboard.html", ses, "tablero", datos=datos,
                   corridas=runner.estado_corridas(),
                   err=error or request.query_params.get("err", ""))


@app.get("/diarios/nuevo", response_class=HTMLResponse)
def form_nuevo(request: Request):
    ses = leer_sesion(request.cookies.get(COOKIE))
    if not ses:
        return RedirectResponse("/login", status_code=303)
    return _render(request, "nuevo.html", ses, "nuevo", valores={}, tonos=config_store.TONOS_VALIDOS)


@app.post("/diarios/nuevo")
def crear(request: Request,
          csrf: str = Form(""),
          nombre: str = Form(""),
          slug: str = Form(""),
          base_url: str = Form(""),
          usuario: str = Form(""),
          password: str = Form(""),
          dueno: str = Form(""),
          telegram_id: str = Form(""),
          horarios: str = Form("08:00, 20:00"),
          notas_por_corrida: int = Form(1),
          tonos: List[str] = Form([]),
          sitio_dir: str = Form(""),
          preparar_wp: str = Form(""),
          instalar_plugin: str = Form(""),
          forzar: str = Form(""),
          borrador: str = Form("")):
    ses = leer_sesion(request.cookies.get(COOKIE))
    if not ses:
        return RedirectResponse("/login", status_code=303)
    if not _csrf_ok(request, csrf):
        return _volver("/diarios/nuevo", err="Sesión vencida, volvé a intentar.")

    valores = _valores_form(nombre, slug, base_url, usuario, dueno, telegram_id, horarios,
                            notas_por_corrida, tonos, sitio_dir, bool(preparar_wp),
                            bool(instalar_plugin), bool(forzar), bool(borrador))
    preparacion: Optional[Dict[str, Any]] = None
    prueba: Optional[Dict[str, Any]] = None

    if preparar_wp:
        if not sitio_dir.strip():
            return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                           tonos=config_store.TONOS_VALIDOS,
                           err="Para preparar el WordPress necesito la carpeta del sitio en el servidor.")
        preparacion = wp_prepare.preparar(sitio_dir.strip(), instalar_plugin=bool(instalar_plugin))

    if not forzar:
        if not usuario.strip() or not password.strip():
            return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                           tonos=config_store.TONOS_VALIDOS, preparacion=preparacion,
                           err="Antes de probar la conexión cargá usuario y contraseña de WordPress.")
        try:
            url = config_store.normalizar_url(base_url)
        except config_store.ErrorValidacion as e:
            return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                           tonos=config_store.TONOS_VALIDOS, preparacion=preparacion, err=str(e))
        prueba = wp_client.probar_jwt(url, usuario.strip(), password.strip())
        if not prueba.get("ok"):
            return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                           tonos=config_store.TONOS_VALIDOS, prueba=prueba, preparacion=preparacion,
                           err="La prueba de conexión falló. Revisá el detalle y si querés forzá el alta.")

    try:
        resultado = config_store.crear_diario({
            "nombre": nombre, "slug": slug, "base_url": base_url,
            "usuario": usuario, "password": password, "dueno": dueno,
            "telegram_id": telegram_id, "horarios": horarios,
            "tonos": tonos, "notas_por_corrida": notas_por_corrida,
        })
    except config_store.ErrorValidacion as e:
        return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                       tonos=config_store.TONOS_VALIDOS, prueba=prueba,
                       preparacion=preparacion, err=str(e))
    except Exception as e:
        return _render(request, "nuevo.html", ses, "nuevo", valores=valores,
                       tonos=config_store.TONOS_VALIDOS, prueba=prueba, preparacion=preparacion,
                       err="No pude guardar el diario: %s" % str(e)[:180])

    mensaje = "Diario creado. Ya queda programado automáticamente."
    if borrador:
        if prueba and prueba.get("token"):
            draft = wp_client.crear_borrador(resultado["entry"], prueba["token"])
            mensaje += " " + (draft.get("mensaje") or "")
        else:
            mensaje += " No pude crear el borrador de prueba (no hubo login JWT en esta alta)."
    return _volver("/diarios/%s" % resultado["slug"], ok=mensaje)


@app.post("/api/probar")
async def api_probar(request: Request):
    ses = leer_sesion(request.cookies.get(COOKIE))
    if not ses:
        return JSONResponse({"ok": False, "mensaje": "Sesión vencida."}, status_code=401)
    try:
        cuerpo = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "mensaje": "Pedido inválido."}, status_code=400)
    try:
        url = config_store.normalizar_url(cuerpo.get("base_url") or "")
    except config_store.ErrorValidacion as e:
        return JSONResponse({"ok": False, "estado": "url", "mensaje": str(e)})
    resultado = wp_client.probar_jwt(url, (cuerpo.get("usuario") or "").strip(),
                                     (cuerpo.get("password") or "").strip())
    if resultado.get("ok"):
        sitio = wp_client.info_sitio(url)
        resultado["sitio"] = sitio.get("nombre")
    return JSONResponse(resultado)


@app.get("/diarios/{slug}", response_class=HTMLResponse)
def ver_diario(request: Request, slug: str):
    ses = leer_sesion(request.cookies.get(COOKIE))
    if not ses:
        return RedirectResponse("/login", status_code=303)
    try:
        fila = stats.detalle(slug)
    except Exception as e:
        return _volver("/", err="No pude leer el diario: %s" % str(e)[:160])
    if not fila:
        return _volver("/", err="El diario '%s' no existe." % slug)
    return _render(request, "diario.html", ses, "tablero", fila=fila,
                   corridas=runner.estado_corridas())


@app.post("/diarios/{slug}/correr")
def correr(request: Request, slug: str, csrf: str = Form(""), notas: int = Form(1)):
    if not leer_sesion(request.cookies.get(COOKIE)):
        return RedirectResponse("/login", status_code=303)
    if not _csrf_ok(request, csrf):
        return _volver("/diarios/%s" % slug, err="Sesión vencida, volvé a intentar.")
    resultado = runner.encolar(slug, notas, motivo="manual")
    return _volver("/diarios/%s" % slug, ok=resultado.get("mensaje", "")) if resultado.get("ok") \
        else _volver("/diarios/%s" % slug, err=resultado.get("mensaje", "No se pudo encolar."))


@app.post("/diarios/{slug}/activar")
def activar(request: Request, slug: str, csrf: str = Form(""), activo: str = Form("1")):
    if not leer_sesion(request.cookies.get(COOKIE)):
        return RedirectResponse("/login", status_code=303)
    if not _csrf_ok(request, csrf):
        return _volver("/diarios/%s" % slug, err="Sesión vencida, volvé a intentar.")
    try:
        config_store.set_activo(slug, activo == "1")
    except config_store.ErrorValidacion as e:
        return _volver("/diarios/%s" % slug, err=str(e))
    return _volver("/diarios/%s" % slug,
                   ok="Diario activado." if activo == "1" else "Diario pausado: no se van a publicar notas.")


@app.post("/diarios/{slug}/eliminar")
def eliminar(request: Request, slug: str, csrf: str = Form(""), confirmar: str = Form("")):
    if not leer_sesion(request.cookies.get(COOKIE)):
        return RedirectResponse("/login", status_code=303)
    if not _csrf_ok(request, csrf):
        return _volver("/diarios/%s" % slug, err="Sesión vencida, volvé a intentar.")
    if confirmar.strip() != slug:
        return _volver("/diarios/%s" % slug, err="Para eliminar, escribí el slug exacto del diario.")
    try:
        config_store.eliminar_diario(slug)
    except config_store.ErrorValidacion as e:
        return _volver("/diarios/%s" % slug, err=str(e))
    return _volver("/", ok="Diario '%s' eliminado. Las claves quedan en config/secrets.env." % slug)


@app.get("/healthz")
def healthz():
    return {"ok": True, "scheduler": runner.scheduler_activo(), "diarios": len(config_store.load_diarios())}
