from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
import time

CHROMEDRIVER_PATH = "./chromedriver/chromedriver.exe"

# Configuración del driver de Chrome
def configurar_driver(chromedriver_path):
    options = webdriver.ChromeOptions()
    # No ejecutamos en modo headless para que el navegador quede abierto
    # options.add_argument("--headless")  # Lo comentamos para que se vea el navegador
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    
    service = Service(chromedriver_path)
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# Función para procesar texto con ChatGPT web
def procesar_con_chatgpt(texto, chromedriver_path):
    driver = configurar_driver(chromedriver_path)
    
    try:
        # Acceder a la web de ChatGPT
        driver.get("https://chat.openai.com/")  # Cambiar por la URL correcta si es necesario

        # Esperar a que cargue la página (ajustar el tiempo según conexión)
        time.sleep(5)

        # Si requiere login, completar aquí el flujo de inicio de sesión
        
        # Buscar el cuadro de texto donde se ingresa el prompt (usando el selector adecuado)
        input_box = driver.find_element(By.CSS_SELECTOR, 'div.ProseMirror[contenteditable="true"]')
        input_box.send_keys(texto)

        # Buscar el botón de envío utilizando el nuevo selector completo
        send_button = driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Enviar mensaje"][data-testid="send-button"]')
        send_button.click()

        # Esperar la respuesta (ajustar tiempo según velocidad de la web)
        time.sleep(10)

        # Capturar la respuesta generada
        respuestas = driver.find_elements(By.CSS_SELECTOR, ".text-sm")  # Ajustar selector si cambia
        respuesta = respuestas[-1].text if respuestas else "No se pudo obtener respuesta."

        # Mostrar la respuesta en la pantalla
        print("Respuesta del ChatGPT:", respuesta)

        # Mantener el navegador abierto para que se vea la respuesta
        input("Presiona Enter para cerrar el navegador...")

    except Exception as e:
        print(f"Error procesando el texto: {str(e)}")
    
    

# Llamada a la función con el texto a procesar
texto_prompt = "¿Cómo está el clima hoy?"
procesar_con_chatgpt(texto_prompt, CHROMEDRIVER_PATH)
