import os
import json
from typing import Dict, Any, List, Optional

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ConversationHandler, ContextTypes

from core.diarios import diarios_for_user, get_diario
from uploading.wp_client import publicar

# ===== Estados =====
ELEGIR_DIARIO, ELEGIR_SCRAPER, VER_LISTA, ELEGIR_ARTICULO, PEDIR_IMAGEN, CONFIRMAR = range(6)

# ===== Util =====
def _kb(options: List[str]) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([[KeyboardButton(o)] for o in options], resize_keyboard=True, one_time_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    context.user_data.clear()
    disponibles = diarios_for_user(uid)
    if not disponibles:
        await update.message.reply_text("No tenés diarios asignados. Pedile a admin que te habilite.")
        return ConversationHandler.END
    # guardar opciones
    context.user_data["diarios"] = disponibles
    opts = [f"{slug} — {cfg['name']}" for slug, cfg in disponibles.items()]
    await update.message.reply_text("Elegí el diario con el que querés trabajar:", reply_markup=_kb(opts))
    return ELEGIR_DIARIO

async def elegir_diario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.split(" — ")[0].strip()
    diario = get_diario(txt)
    if not diario:
        await update.message.reply_text("Diario inválido. Probá de nuevo.")
        return ELEGIR_DIARIO
    context.user_data["diario_slug"] = txt
    await update.message.reply_text("Elegí la fuente:", reply_markup=_kb(["LinkedIn", "La Nación", "Infobae", "Carga manual"]))
    return ELEGIR_SCRAPER

async def elegir_scraper(update: Update, context: ContextTypes.DEFAULT_TYPE):
    src = update.message.text.strip().lower()
    context.user_data["source"] = src
    # Leer de archivo generado por scraping (para simplificar la demo)
    data = []
    if os.path.exists("salidas_scraper.json"):
        try:
            data = json.load(open("salidas_scraper.json", "r", encoding="utf-8"))
        except Exception:
            data = []
    if not data:
        await update.message.reply_text("No hay resultados listos. Corré el scraper y probá nuevamente.")
        return ELEGIR_SCRAPER
    context.user_data["lista"] = data[:10]
    # Mostrar top 10
    lines = [f"{i+1}. {a.get('titulo','(sin título)')}" for i,a in enumerate(context.user_data['lista'])]
    await update.message.reply_text("Resultados:\n" + "\n".join(lines))
    await update.message.reply_text("Mandame el número del artículo que querés usar.")
    return ELEGIR_ARTICULO

async def elegir_articulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(update.message.text) - 1
    except Exception:
        await update.message.reply_text("Decime un número válido (1..10).")
        return ELEGIR_ARTICULO
    lista = context.user_data.get("lista", [])
    if not (0 <= idx < len(lista)):
        await update.message.reply_text("Fuera de rango. Probá de nuevo.")
        return ELEGIR_ARTICULO
    art = lista[idx]
    context.user_data["titulo"] = art.get("titulo","(sin título)")
    context.user_data["cuerpo"] = art.get("descripcion","")
    await update.message.reply_text(f"Título elegido:\n{context.user_data['titulo']}\n\nAhora mandame una imagen (como foto).")
    return PEDIR_IMAGEN

async def recibir_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        await update.message.reply_text("Necesito que me mandes una imagen como foto.")
        return PEDIR_IMAGEN
    file = await photo.get_file()
    os.makedirs("imagenes", exist_ok=True)
    img_path = os.path.join("imagenes", f"{update.effective_user.id}_last.jpg")
    await file.download_to_drive(img_path)
    context.user_data["imagen_path"] = img_path
    await update.message.reply_text("¿Publico con este contenido?\n\nResponé: SI / NO", reply_markup=_kb(["SI","NO"]))
    return CONFIRMAR

async def confirmar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resp = update.message.text.strip().lower()
    if resp not in ("si","sí","no"):
        await update.message.reply_text("Decime SI o NO.")
        return CONFIRMAR
    if resp.startswith("n"):
        await update.message.reply_text("Cancelado. /start para comenzar de nuevo.")
        return ConversationHandler.END
    # Publicar
    slug = context.user_data["diario_slug"]
    diario_cfg = get_diario(slug)
    try:
        post_id = publicar(diario_cfg, context.user_data["titulo"], context.user_data["cuerpo"], context.user_data.get("imagen_path"))
    except Exception as e:
        await update.message.reply_text(f"Falló la publicación: {e}")
        return ConversationHandler.END
    await update.message.reply_text(f"Listo. Publicación creada (ID {post_id}) en {diario_cfg['name']}.")
    return ConversationHandler.END

def build_app(token: str):
    app = Application.builder().token(token).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            ELEGIR_DIARIO: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_diario)],
            ELEGIR_SCRAPER: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_scraper)],
            VER_LISTA: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_scraper)],
            ELEGIR_ARTICULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, elegir_articulo)],
            PEDIR_IMAGEN: [MessageHandler(filters.PHOTO, recibir_imagen)],
            CONFIRMAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar)],
        },
        fallbacks=[CommandHandler("start", start)],
        name="notibot_v2",
        persistent=False,
    )
    app.add_handler(conv)
    return app

if __name__ == "__main__":
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    if not TOKEN:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en el entorno.")
    build_app(TOKEN).run_polling()
