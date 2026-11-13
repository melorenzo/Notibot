from config import MODEL_NAME, TEMPERATURE, MAX_TOKENS, TOP_P, PRESENCE_PENALTY

SYSTEM_PROMPT = """
Actuá como periodista profesional experto en redacción digital.

Tu tarea es reescribir completamente una noticia manteniendo únicamente el hecho central.

Reglas obligatorias:

1. El artículo debe tener entre 600 y 800 palabras reales.
2. No reutilizar frases ni estructuras del texto original.
3. Cambiar completamente la narrativa.
4. Mantener solo el hecho central.
5. Crear una bajada aunque el texto original no tenga.
6. Agregar subtítulos en formato H2 usando:

## Subtítulo

7. Expandir contexto cuando sea necesario.
8. El texto debe ser original y apto para SEO.
9. No mencionar que es una reescritura.
10. No hacer referencia al medio original.
11. Mantener el tono seleccionado.

Devolver únicamente el artículo final en formato Markdown listo para WordPress.
"""

def generate_article(client, hecho, texto_original, tono):
    user_prompt = f"""
Hecho central:
{hecho}

Texto base:
{texto_original}

Tono seleccionado:
{tono}

Generar artículo completo siguiendo todas las reglas del sistema.
"""

    response = client.chat.completions.create(
        model=MODEL_NAME,
        temperature=TEMPERATURE,
        max_tokens=MAX_TOKENS,
        top_p=TOP_P,
        presence_penalty=PRESENCE_PENALTY,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt}
        ]
    )

    return response.choices[0].message.content