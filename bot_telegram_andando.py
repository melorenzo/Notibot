import json
import os
import asyncio
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ConversationHandler,
    CallbackContext,
)
from scraping.scraper_linkd import scrape_linkedin
from scraping.scraper_lanacion import scrape_lanacion
from scraping.scraper_infobae import scrape_infobae
from procesing.procesar_gemini import procesar_con_gemini
from uploading.subir_wordpress_tinta_bot import ejecutar_publicacion_tinta, subir_imagen_tinta, publicar_en_wordpress_tinta
from uploading.subir_wordpress_palabras_bot import ejecutar_publicacion_palabras, subir_imagen_palabras, publicar_en_wordpress_palabras
from uploading.subir_wordpress_la_voz import ejecutar_publicacion_la_voz, subir_imagen_la_voz, publicar_en_wordpress_la_voz
from scraping.scraper_mano_bot import agregar_noticia_a_json
from scraping.scraper_x import get_latest_tweets

# URL y path al Chromedriver
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"

# Estados de la conversación
MENU_PRINCIPAL, SUBMENU_SCRAPING, SUBMENU_PUBLICACION, SELECCIONAR_ARTICULO, SELECCIONAR_PUBLICACION, RECIBIR_IMAGEN, CONFIRMAR_PUBLICACION, RECIBIR_TITULO, RECIBIR_DESCRIPCION, SUBMENU_TWITTER = range(10)

# Ruta de la carpeta para guardar imágenes
CARPETA_IMAGENES = "/app/uploading/imagenes_subir"

# === Funciones auxiliares ===
def leer_archivo_json(filepath):
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None

async def mostrar_articulos(articulos, update: Update):
    for i, articulo in enumerate(articulos, 1):
        mensaje = f"{i}. Título: {articulo['titulo']}\nDescripción: {articulo['descripcion']}\n"
        await update.message.reply_text(mensaje)

def seleccionar_articulo(articulos, update: Update):
    try:
        seleccion = int(update.message.text)
        if 1 <= seleccion <= len(articulos):
            return articulos[seleccion - 1]
        else:
            update.message.reply_text("Selección inválida. Por favor, selecciona un número válido.")
    except ValueError:
        update.message.reply_text("Entrada no válida. Por favor, ingresa un número.")
    return None

# === Guardar imagen recibida ===
async def guardar_imagen(update: Update, context: CallbackContext):
    # Obtener la imagen enviada por el usuario
    file = await update.message.photo[-1].get_file()
    ruta_imagen = os.path.join(CARPETA_IMAGENES, "imagen.jpg")
    await file.download_to_drive(ruta_imagen)
    await update.message.reply_text("Imagen guardada correctamente.")
    return SELECCIONAR_PUBLICACION

# === Manejar la selección de publicación ===
async def seleccionar_publicacion_handler(update: Update, context: CallbackContext):
    opcion = update.message.text
    ruta_imagen = os.path.join(CARPETA_IMAGENES, "imagen.jpg")

    # Verificar si la imagen existe
    if not os.path.exists(ruta_imagen):
        await update.message.reply_text("❌ Error: No se encontró una imagen. La publicación no se realizará sin una imagen.")
        return MENU_PRINCIPAL

    if opcion == "1. Publicar en La Tinta":
        await update.message.reply_text("Publicando en La Tinta...")
        # Llamar a la función específica para La Tinta
        titulo, cuerpo = ejecutar_publicacion_tinta("/app/noticia_generada.json", ruta_imagen)
        if titulo and cuerpo:
            context.user_data['titulo'] = titulo
            context.user_data['cuerpo'] = cuerpo
            context.user_data['ruta_imagen'] = ruta_imagen
            context.user_data['sitio'] = "La Tinta"  # Guardar el sitio seleccionado
            await update.message.reply_text("=== Previsualización de la Publicación ===")
            await update.message.reply_text(f"Título: {titulo}")
            await update.message.reply_text(f"Cuerpo:\n{cuerpo}")
            await update.message.reply_text("==========================================")
            await update.message.reply_text("¿Deseas publicar este contenido? (sí/no)")
            return CONFIRMAR_PUBLICACION
        else:
            await update.message.reply_text("No se pudo generar la previsualización.")
            return MENU_PRINCIPAL

    elif opcion == "2. Publicar en Palabras en la Red":
        await update.message.reply_text("Publicando en Palabras en la Red...")
        # Llamar a la función específica para Palabras en la Red
        titulo, cuerpo = ejecutar_publicacion_palabras("/app/noticia_generada.json", ruta_imagen)
        if titulo and cuerpo:
            context.user_data['titulo'] = titulo
            context.user_data['cuerpo'] = cuerpo
            context.user_data['ruta_imagen'] = ruta_imagen
            context.user_data['sitio'] = "Palabras en la Red"  # Guardar el sitio seleccionado
            await update.message.reply_text("=== Previsualización de la Publicación ===")
            await update.message.reply_text(f"Título: {titulo}")
            await update.message.reply_text(f"Cuerpo:\n{cuerpo}")
            await update.message.reply_text("==========================================")
            await update.message.reply_text("¿Deseas publicar este contenido? (sí/no)")
            return CONFIRMAR_PUBLICACION
        else:
            await update.message.reply_text("No se pudo generar la previsualización.")
            return MENU_PRINCIPAL
    
    elif opcion == "3. Publicar en La Voz Porteña":
        await update.message.reply_text("Publicando en La Voz Porteña...")
        # Llamar a la función específica para La Voz porteña
        titulo, cuerpo = ejecutar_publicacion_la_voz("/app/noticia_generada.json", ruta_imagen)
        if titulo and cuerpo:
            context.user_data['titulo'] = titulo
            context.user_data['cuerpo'] = cuerpo
            context.user_data['ruta_imagen'] = ruta_imagen
            context.user_data['sitio'] = "La Voz Porteña"  # Guardar el sitio seleccionado
            await update.message.reply_text("=== Previsualización de la Publicación ===")
            await update.message.reply_text(f"Título: {titulo}")
            await update.message.reply_text(f"Cuerpo:\n{cuerpo}")
            await update.message.reply_text("==========================================")
            await update.message.reply_text("¿Deseas publicar este contenido? (sí/no)")
            return CONFIRMAR_PUBLICACION
        else:
            await update.message.reply_text("No se pudo generar la previsualización.")
            return MENU_PRINCIPAL    

    elif opcion == "4. Volver al menú principal":
        return await start(update, context)

    else:
        await update.message.reply_text("Opción no válida. Por favor, elige una opción del submenú.")
        return MENU_PRINCIPAL

# === Manejar la confirmación de publicación ===
async def confirmar_publicacion(update: Update, context: CallbackContext):
    respuesta = update.message.text.lower()

    if respuesta in ["sí", "si", "s"]:
        titulo = context.user_data.get('titulo')
        cuerpo = context.user_data.get('cuerpo')
        ruta_imagen = context.user_data.get('ruta_imagen')
        sitio = context.user_data.get('sitio')  # Obtener el sitio seleccionado

        # Verificar si la imagen existe
        if not ruta_imagen or not os.path.exists(ruta_imagen):
            await update.message.reply_text("❌ Error: No se encontró una imagen. La publicación no se realizará sin una imagen.")
            return MENU_PRINCIPAL
              
        # Publicar en WordPress según el sitio seleccionado
        if sitio == "La Tinta":
            imagen_id = subir_imagen_tinta(ruta_imagen)
            if not imagen_id:
                await update.message.reply_text("❌ Error al subir la imagen. La publicación no se realizará.")
                return MENU_PRINCIPAL
            exito = publicar_en_wordpress_tinta(titulo, cuerpo, imagen_id)  # Función específica para La Tinta
        elif sitio == "Palabras en la Red":
            imagen_id = subir_imagen_palabras(ruta_imagen)  
            if not imagen_id:
                await update.message.reply_text("❌ Error al subir la imagen. La publicación no se realizará.")
                return MENU_PRINCIPAL
            exito = publicar_en_wordpress_palabras(titulo, cuerpo, imagen_id)  # Función específica para Palabras en la Red
        elif sitio == "La Voz Porteña":
            imagen_id = subir_imagen_la_voz(ruta_imagen)  
            if not imagen_id:
                await update.message.reply_text("❌ Error al subir la imagen. La publicación no se realizará.")
                return MENU_PRINCIPAL
            exito = publicar_en_wordpress_la_voz(titulo, cuerpo, imagen_id)  # Función específica para  la voz  porteña  
        else:
            await update.message.reply_text("❌ Error: Sitio no reconocido.")
            return MENU_PRINCIPAL

        if exito:
            await update.message.reply_text("✅ Noticia publicada exitosamente.")
            await update.message.reply_text("¡Gracias por utilizar NotiBot! El proceso culminó correctamente. Inicia uno nuevo con /start.")
            return ConversationHandler.END  # Finalizar la conversación
        else:
            await update.message.reply_text("❌ Error al publicar la noticia.")
    else:
        await update.message.reply_text("Publicación cancelada.")

    return MENU_PRINCIPAL

# === Menú principal ===
async def start(update: Update, context: CallbackContext):
    # Mostrar el menú principal
    opciones = [
        ["1. Scraping de LinkedIn", "2. Scraping de otras webs"],
        ["3. Scraping de Twitter", "4. Procesar con Gemini"],
        ["5. Ejecutar Publicación", "6. Agregar Noticia a Mano"]
    ]
    await update.message.reply_text(
        "=== Menú Principal ===\n"
        "Elige una opción:",
        reply_markup=ReplyKeyboardMarkup(opciones, one_time_keyboard=True)
    )
    return MENU_PRINCIPAL

async def menu_principal_handler(update: Update, context: CallbackContext):
    opcion = update.message.text
    if opcion == "1. Scraping de LinkedIn":
        return await scraping_linkedin(update, context)
    elif opcion == "2. Scraping de otras webs":
        return await submenu_scraping(update, context)
    elif opcion == "3. Scraping de Twitter":
        return await submenu_twitter(update, context)
    elif opcion == "4. Procesar con Gemini":
        return await procesar_con_gemini_handler(update, context)
    elif opcion == "5. Ejecutar Publicación":
        return await submenu_publicacion(update, context)
    elif opcion == "6. Agregar Noticia a Mano":
        return await agregar_noticia_handler(update, context)
    else:
        await update.message.reply_text("Opción no válida. Por favor, elige una opción del menú.")
        return MENU_PRINCIPAL

# === Submenú de Scraping ===
async def submenu_scraping(update: Update, context: CallbackContext):
    opciones = [["1. Scraping de La Nación", "2. Scraping de Infobae"], ["3. Volver al menú principal"]]
    await update.message.reply_text(
        "=== Submenú de Scraping ===\n"
        "Elige una opción:",
        reply_markup=ReplyKeyboardMarkup(opciones, one_time_keyboard=True)
    )
    return SUBMENU_SCRAPING

async def submenu_scraping_handler(update: Update, context: CallbackContext):
    opcion = update.message.text
    if opcion == "1. Scraping de La Nación":
        return await scraping_lanacion(update, context)
    elif opcion == "2. Scraping de Infobae":
        return await scraping_infobae(update, context)
    elif opcion == "3. Volver al menú principal":
        return await start(update, context)
    else:
        await update.message.reply_text("Opción no válida. Por favor, elige una opción del submenú.")
        return SUBMENU_SCRAPING

# === Funciones de scraping ===
async def scraping_linkedin(update: Update, context: CallbackContext):
    await update.message.reply_text("Realizando scraping de LinkedIn...")
    articulos = scrape_linkedin(CHROMEDRIVER_PATH)
    if articulos:
        await mostrar_articulos(articulos, update)
        context.user_data['publicaciones'] = articulos
        await update.message.reply_text("Selecciona el número de la noticia que deseas procesar:")
        return SELECCIONAR_ARTICULO
    else:
        await update.message.reply_text("❌ Error: No se encontraron publicaciones en LinkedIn.")
        return MENU_PRINCIPAL

async def scraping_lanacion(update: Update, context: CallbackContext):
    await update.message.reply_text("Realizando scraping de La Nación...")
    articulos = scrape_lanacion(CHROMEDRIVER_PATH)
    if articulos:
        await mostrar_articulos(articulos, update)
        context.user_data['articulos'] = articulos
        await update.message.reply_text("Selecciona el número de la noticia que deseas procesar:")
        return SELECCIONAR_ARTICULO
    else:
        await update.message.reply_text("❌ Error: No se encontraron artículos en La Nación.")
        return MENU_PRINCIPAL

async def scraping_infobae(update: Update, context: CallbackContext):
    await update.message.reply_text("Realizando scraping de Infobae...")
    articulos = scrape_infobae(CHROMEDRIVER_PATH)
    if articulos:
        await mostrar_articulos(articulos, update)
        context.user_data['articulos'] = articulos
        await update.message.reply_text("Selecciona el número de la noticia que deseas procesar:")
        return SELECCIONAR_ARTICULO
    else:
        await update.message.reply_text("❌ Error: No se encontraron artículos en Infobae.")
        return MENU_PRINCIPAL

# === Procesar con Gemini ===
async def procesar_con_gemini_handler(update: Update, context: CallbackContext):
    articulos = leer_archivo_json("salidas_scraper.json")
    if articulos:
        await mostrar_articulos(articulos, update)
        context.user_data['articulos'] = articulos
        await update.message.reply_text("Selecciona el número de la noticia que deseas procesar:")
        return SELECCIONAR_ARTICULO
    else:
        await update.message.reply_text("No se encontraron artículos en el archivo JSON.")
        return MENU_PRINCIPAL

# === Submenú de Publicación ===
async def submenu_publicacion(update: Update, context: CallbackContext):
    opciones = [["1. Publicar en La Tinta", "2. Publicar en Palabras en la Red","3. Publicar en La Voz Porteña"], ["4. Volver al menú principal"]]
    await update.message.reply_text(
        "=== Submenú de Publicación ===\n"
        "Elige una opción:",
        reply_markup=ReplyKeyboardMarkup(opciones, one_time_keyboard=True)
    )
    return SUBMENU_PUBLICACION

async def submenu_publicacion_handler(update: Update, context: CallbackContext):
    opcion = update.message.text
    if opcion == "1. Publicar en La Tinta":
        await update.message.reply_text("Por favor, envía la imagen que deseas publicar.")
        return RECIBIR_IMAGEN
    elif opcion == "2. Publicar en Palabras en la Red":
        await update.message.reply_text("Por favor, envía la imagen que deseas publicar.")
        return RECIBIR_IMAGEN
    elif opcion == "3. Publicar en La Voz Porteña":
        await update.message.reply_text("Por favor, envía la imagen que deseas publicar.")
        return RECIBIR_IMAGEN
    elif opcion == "4. Volver al menú principal":
        return await start(update, context)
    else:
        await update.message.reply_text("Opción no válida. Por favor, elige una opción del submenú.")
    return MENU_PRINCIPAL

# === Agregar noticia manualmente ===
async def agregar_noticia_handler(update: Update, context: CallbackContext):
    await update.message.reply_text("Por favor, ingresa el título de la noticia:")
    return RECIBIR_TITULO

async def recibir_titulo(update: Update, context: CallbackContext):
    context.user_data['titulo'] = update.message.text
    await update.message.reply_text("Ahora, ingresa la descripción de la noticia:")
    return RECIBIR_DESCRIPCION

async def recibir_descripcion(update: Update, context: CallbackContext):
    titulo = context.user_data.get('titulo')
    descripcion = update.message.text

    # Guardar la noticia en el JSON
    agregar_noticia_a_json(titulo, descripcion)

    await update.message.reply_text("✅ Noticia agregada correctamente. Puedes iniciar un nuevo proceso con /start.")
    return ConversationHandler.END

# === Seleccionar artículo ===
async def seleccionar_articulo_handler(update: Update, context: CallbackContext):
    articulos = context.user_data.get('articulos', [])
    articulo_seleccionado = seleccionar_articulo(articulos, update)
    if articulo_seleccionado:
        context.user_data['articulo_seleccionado'] = articulo_seleccionado
        await update.message.reply_text("Procesando la noticia seleccionada con Gemini...")
        
        # Obtener el índice de la noticia seleccionada
        indice_noticia = articulos.index(articulo_seleccionado)
        
        # Procesar la noticia y eliminar del JSON original
        texto_para_procesar = articulo_seleccionado['descripcion']
        respuesta = procesar_con_gemini(
            texto_para_procesar,
            ruta_json_original="salidas_scraper.json",  # Ruta del JSON original
            indice_noticia=indice_noticia               # Índice de la noticia seleccionada
        )
        
        await update.message.reply_text(f"=== Respuesta procesada ===\n{respuesta}")
        context.user_data['respuesta_procesada'] = respuesta
        return await submenu_publicacion(update, context)
    else:
        await update.message.reply_text("No se seleccionó un artículo válido.")
        return MENU_PRINCIPAL

# === Cancelar ===
async def cancel(update: Update, context: CallbackContext):
    await update.message.reply_text("Proceso cancelado. Usa /start para volver al menú principal.")
    return ConversationHandler.END

# === Submenú de Twitter ===
async def submenu_twitter(update: Update, context: CallbackContext):
    opciones = [["1. Scraping Twitter Macri", "2. Scraping Twitter GCBA"], ["3. Volver al menú principal"]]
    await update.message.reply_text(
        "=== Submenú de Twitter ===\n"
        "Elige una opción:",
        reply_markup=ReplyKeyboardMarkup(opciones, one_time_keyboard=True)
    )
    return SUBMENU_TWITTER

async def submenu_twitter_handler(update: Update, context: CallbackContext):
    opcion = update.message.text
    if opcion == "1. Scraping Twitter Macri":
        await update.message.reply_text("Realizando scraping de Twitter (Jorge Macri)...")
        tweets = get_latest_tweets(["jorgemacri"])
        if tweets:
            await mostrar_articulos(tweets, update)
            context.user_data['articulos'] = tweets
            await update.message.reply_text("Selecciona el número del tweet que deseas procesar:")
            return SELECCIONAR_ARTICULO
        else:
            await update.message.reply_text("❌ Error: No se encontraron tweets de Jorge Macri.")
            return MENU_PRINCIPAL
    elif opcion == "2. Scraping Twitter GCBA":
        await update.message.reply_text("Realizando scraping de Twitter (GCBA)...")
        tweets = get_latest_tweets(["gcba"])
        if tweets:
            await mostrar_articulos(tweets, update)
            context.user_data['articulos'] = tweets
            await update.message.reply_text("Selecciona el número del tweet que deseas procesar:")
            return SELECCIONAR_ARTICULO
        else:
            await update.message.reply_text("❌ Error: No se encontraron tweets del GCBA.")
            return MENU_PRINCIPAL
    elif opcion == "3. Volver al menú principal":
        return await start(update, context)
    else:
        await update.message.reply_text("Opción no válida. Por favor, elige una opción del submenú.")
        return SUBMENU_TWITTER

# === Configuración del bot ===
def main():
    # El token vive en variables.env (TELEGRAM_BOT_TOKEN), nunca en el código.
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        raise SystemExit("Falta TELEGRAM_BOT_TOKEN en el entorno.")
    application = Application.builder().token(token).build()

    # Conversación principal
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MENU_PRINCIPAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, menu_principal_handler)],
            SUBMENU_SCRAPING: [MessageHandler(filters.TEXT & ~filters.COMMAND, submenu_scraping_handler)],
            SUBMENU_TWITTER: [MessageHandler(filters.TEXT & ~filters.COMMAND, submenu_twitter_handler)],
            SUBMENU_PUBLICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, submenu_publicacion_handler)],
            SELECCIONAR_ARTICULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, seleccionar_articulo_handler)],
            RECIBIR_IMAGEN: [MessageHandler(filters.PHOTO, guardar_imagen)],
            SELECCIONAR_PUBLICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, seleccionar_publicacion_handler)],
            CONFIRMAR_PUBLICACION: [MessageHandler(filters.TEXT & ~filters.COMMAND, confirmar_publicacion)],
            RECIBIR_TITULO: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_titulo)],
            RECIBIR_DESCRIPCION: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_descripcion)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    application.add_handler(conv_handler)

    # Iniciar el bot
    application.run_polling()

if __name__ == '__main__':
   main()
