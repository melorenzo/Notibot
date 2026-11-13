#!/usr/bin/env python3
"""Parche: avisos de 'Publicado' a IDs de Telegram extra (solo workers que lo activen).
Uso (en la carpeta app2-notibot-V2):  python3 apply_notify_patch.py
Hace backup de cron_base.py y es idempotente."""
import pathlib, re, shutil, sys, time

p = pathlib.Path("cron_base.py")
s = p.read_text(encoding="utf-8")
if "NOTIFY_EXTRA_IDS" in s:
    sys.exit("cron_base.py ya tiene el parche, no toco nada.")

def once(old, new, label):
    global s
    if s.count(old) != 1:
        sys.exit(f"No pude ubicar el ancla '{label}' (apariciones: {s.count(old)}). Cancelado, no se modificó nada.")
    s = s.replace(old, new)

# 1) variable global con los IDs extra
m = re.search(r"^UP_DIR\s*=.*$", s, flags=re.M)
if not m:
    sys.exit("No encontré UP_DIR. Cancelado.")
s = s[:m.end()] + "\n\n# IDs de Telegram extra que reciben los avisos de publicación (los setea cada worker)\nNOTIFY_EXTRA_IDS: List[str] = []" + s[m.end():]

# 2) tg_send acepta extras=True
once('def tg_send(text: str, log: logging.Logger,\n            reply_markup: Optional[dict] = None) -> bool:',
     'def tg_send(text: str, log: logging.Logger,\n            reply_markup: Optional[dict] = None, extras: bool = False) -> bool:',
     "firma tg_send")
once('        if reply_markup:\n            payload["reply_markup"] = json.dumps(reply_markup)\n',
     '        if reply_markup:\n            payload["reply_markup"] = json.dumps(reply_markup)\n        if extras:\n            tg_send_extra(text, log)\n',
     "payload reply_markup")

# 3) función que avisa a los extras (sin botones; nunca rompe la corrida)
once('def tg_success(',
'''def tg_send_extra(text: str, log: logging.Logger) -> None:
    """Envía el mismo aviso a los IDs extra del worker. Sin botones. Nunca lanza excepciones."""
    if not TELEGRAM_TOKEN:
        return
    for cid in NOTIFY_EXTRA_IDS:
        if not cid or cid == TELEGRAM_USER_ID:
            continue
        try:
            r = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": cid, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": False},
                timeout=15,
            )
            if r.status_code != 200:
                log.warning("Telegram: no pude avisar a %s (HTTP %s): %s",
                            cid, r.status_code, r.text[:120])
        except Exception as e:
            log.warning("Telegram: error avisando a %s: %s", cid, e)


def tg_success(''', "def tg_success")

# 4) solo el aviso de éxito va a los extras (errores y fallbacks quedan solo para el admin)
once('        log,\n        reply_markup=reply_markup,\n    )',
     '        log,\n        reply_markup=reply_markup,\n        extras=True,\n    )', "llamada de tg_success")

shutil.copy2(p, f"cron_base.py.bak_{time.strftime('%Y%m%d_%H%M%S')}")
p.write_text(s, encoding="utf-8")
print("ok: cron_base.py parcheado (backup creado)")
