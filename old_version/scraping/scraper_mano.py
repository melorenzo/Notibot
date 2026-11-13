import json
import os

def agregar_noticia_a_json():
    """
    Solicita al usuario un título y una descripción, y los guarda en el archivo salidas_scraper.json.
    Si el archivo ya existe, agrega los datos nuevos sin eliminar los existentes.
    """
    # Solicitar inputs del usuario
    titulo = input("Ingresa el título de la noticia: ")
    descripcion = input("Ingresa la descripción de la noticia: ")

    # Crear un diccionario con los datos
    nueva_noticia = {
        "titulo": titulo,
        "descripcion": descripcion
    }

    # Verificar si el archivo JSON existe
    if os.path.exists("salidas_scraper.json"):
        # Cargar datos existentes del archivo
        with open("salidas_scraper.json", "r", encoding="utf-8") as f:
            noticias = json.load(f)
    else:
        # Crear una lista vacía si el archivo no existe
        noticias = []

    # Agregar la nueva noticia a la lista
    noticias.append(nueva_noticia)

    # Guardar la lista actualizada en el archivo JSON
    with open("salidas_scraper.json", "w", encoding="utf-8") as f:
        json.dump(noticias, f, ensure_ascii=False, indent=4)

    print("La noticia se ha guardado exitosamente en 'salidas_scraper.json'.")
