from openai import OpenAI
import os
import json

# Cargar la clave de la API de OpenAI desde las variables de entorno
api_key = os.getenv("OPENAI_API_KEY")

# Crear cliente de OpenAI
client = OpenAI(api_key=api_key)

# Función para interactuar con la API de OpenAI
def procesar_con_chatgpt(textos):
    try:
        # Crear el prompt que se pasará a la API
        prompt = "Genera una noticia con la siguiente info porfa : " + "\n".join(textos)

        # Llamada a la API de OpenAI utilizando el nuevo cliente
        completion = client.chat.completions.create(
            model="gpt-3.5-turbo",  # Puedes cambiar el modelo si es necesario
            store=True,
            messages=[{"role": "user", "content": prompt}]
        )
        
        # Obtener la respuesta generada por ChatGPT
        respuesta = completion.choices[0].message.content.strip()
        return respuesta

    except Exception as e:
        print(f"Error al procesar con ChatGPT: {e}")
        return None

# Función para cargar los datos del archivo JSON
def cargar_articulos_json(nombre_archivo):
    try:
        with open(nombre_archivo, 'r', encoding='utf-8') as file:
            articulos = json.load(file)
        return articulos
    except Exception as e:
        print(f"Error al cargar el archivo JSON: {e}")
        return []

# Función para procesar con ChatGPT desde el menú
def procesar_menu_con_chatgpt():
    # Cargar los artículos desde el archivo JSON
    articulos = cargar_articulos_json("salidas_scraper_infobae.json")  # Cambia el nombre del archivo si es necesario
    if not articulos:
        print("No se encontraron artículos para procesar.")
        return
    
    textos_para_enviar = []
    
    # Mostrar los artículos y pedir que se elijan para procesar
    print("=== Selecciona los artículos para procesar ===")
    for i, articulo in enumerate(articulos):
        print(f"{i+1}. Título: {articulo['titulo']}")
        print(f"   Descripción: {articulo['descripcion']}\n")

    try:
        seleccion = input(f"Selecciona los números de los artículos para procesar (ej. 1,2): ")
        seleccion_indices = [int(i)-1 for i in seleccion.split(",")]
        textos_para_enviar = [articulos[i]['descripcion'] for i in seleccion_indices]

        # Llamada a la función para procesar con ChatGPT
        respuesta_chatgpt = procesar_con_chatgpt(textos_para_enviar)
        if respuesta_chatgpt:
            print(f"Respuesta de ChatGPT:\n{respuesta_chatgpt}")
        else:
            print("No se pudo procesar con ChatGPT.")
    except ValueError:
        print("Entrada no válida. Debes ingresar números separados por comas.")
