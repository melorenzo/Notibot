# -*- coding: utf-8 -*-
"""
Procesamiento con Groq para Notibot (reemplaza a Gemini).

Objetivo del "refrito" V3.5:
- Generar una nota NUEVA, distinta a la fuente, con buena estructura editorial.
- Largo objetivo: 600 a 800 palabras.
- Cambiar estructura narrativa y NO reutilizar frases del original.
- Conservar solo el hecho central.
- Crear bajada aunque no exista.
- Agregar subtítulos H2 estilo WordPress (## ...).
- Expandir contexto cuando haga falta (sin inventar datos sensibles).
- Salida estricta: JSON con claves: titulo, cuerpo, imagen_url, tags
"""
from __future__ import annotations

import os
import json
from typing import Dict

# SDK de Groq (reemplaza a google-genai)
# pip: groq
from groq import Groq


# -------------------------
# Tonos (se eligen en el flujo o al azar en el cron)
# -------------------------
TONOS: Dict[str, str] = {
    "🔥 Entusiasta": "Energía alta, optimista, cercano. Ritmo ágil. Evitá exageraciones infantiles.",
    "🧩 Realista":   "Sobrio, concreto, sin adornos de más. Priorizá claridad y contexto.",
    "📰 Serio":      "Formal, periodístico clásico. Enfoque informativo, vocabulario cuidado.",
    "🎭 Irónico":    "Toque irónico sutil (sin agresión). Humor fino y elegante, sin faltar el respeto.",
}

# -------------------------
# Modos narrativos (nuevos en V4 — se eligen al azar en el cron)
# -------------------------
MODOS_NARRATIVOS: Dict[str, str] = {
    "informativo": (
        "📋 Informativo: ceñite estrictamente a los hechos que aparecen en la nota base. "
        "No agregues opinión, no saques conclusiones propias, no sumes datos externos. "
        "Periodismo puro: quién, qué, cuándo, dónde, cómo. "
        "El cierre debe resumir el hecho, no evaluar ni proyectar."
    ),
    "con_datos": (
        "📊 Con datos: rescatá 1 o 2 datos concretos que aparezcan en la nota base "
        "(cifras, fechas, porcentajes, cantidades) y dales protagonismo en el desarrollo. "
        "Usá esos datos como ancla periodística. El resto es reescritura libre con contexto general. "
        "No inventes datos que no estén en la fuente."
    ),
    "con_conclusion": (
        "💡 Con conclusión: desarrollá el artículo normalmente y cerrá con un párrafo de cierre editorial "
        "donde elaborés una reflexión o conclusión propia, fundamentada en el hecho informado. "
        "La conclusión debe ser sobria y argumentada, nunca grandilocuente ni obvia."
    ),
    "de_opinion": (
        "🗣️ De opinión: tomá una postura leve pero clara sobre el tema de la nota. "
        "No seas neutro, pero tampoco panfletario. Argumentá brevemente tu punto de vista "
        "intercalado en el desarrollo. La postura debe surgir naturalmente del texto, "
        "no ser una declaración aparte. Usá primera persona del plural (nosotros/nos) si ayuda."
    ),
}


def _get_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY", "").strip()

    if not api_key:
        raise RuntimeError("❌ GROQ_API_KEY no configurada. Andá a https://console.groq.com y creá una API key.")

    print("[GROQ] Modo: API KEY")
    return Groq(api_key=api_key)


def _build_prompt(titulo: str, cuerpo: str, tono: str, modo: str = "informativo") -> str:
    tono_instr = TONOS.get(tono, tono)
    modo_instr = MODOS_NARRATIVOS.get(modo, MODOS_NARRATIVOS["informativo"])

    return f"""Sos un/a redactor/a profesional para un medio digital de Buenos Aires.
Tenés que REESCRIBIR y AMPLIAR la nota, produciendo un artículo NUEVO y MUY DIFERENTE al original.
Tu objetivo es conservar ÚNICAMENTE el HECHO CENTRAL y reconstruir el artículo con estructura editorial.

REGLAS DURAS (OBLIGATORIAS):
1) La salida DEBE ser SOLO un objeto JSON válido, SIN NADA MÁS.
   - NO incluyas bloques markdown como ```json
   - NO agregues texto antes o después del JSON
   - NO incluyas comentarios
   - Empezá directamente con {{ y terminá con }}

2) El JSON debe tener estas claves exactas:
   {{
     "titulo": "string (55 a 85 caracteres aprox, atractivo, sin repetir el título original)",
     "cuerpo": "string (en español rioplatense, 600 a 800 palabras REALES)",
     "imagen_url": null,
     "tags": ["tag1", "tag2", "tag3", "tag4", "tag5"]
   }}

3) Conservá ÚNICAMENTE el HECHO CENTRAL de la fuente. Lo secundario se descarta o se convierte en contexto general.

4) PROHIBIDO reutilizar frases del original. No copies ni parafrasees pegado. Reescritura total.
   La nota publicada NUNCA puede parecerse a la fuente. El lector no debe notar que existe una nota base.

5) Cambiá completamente la estructura narrativa: arranque distinto, orden distinto, desarrollo distinto y cierre distinto.

6) El "cuerpo" debe arrancar con una BAJADA (2 a 3 oraciones) aunque no exista en la fuente.

7) Debe incluir 3 a 6 subtítulos tipo WordPress en H2 usando exactamente el formato: "## Título del subtítulo".
   Ejemplo en el cuerpo:
   "Esta es la bajada de la nota...
   
   ## Primer subtítulo
   Contenido de la primera sección...
   
   ## Segundo subtítulo
   Contenido de la segunda sección..."

8) Podés sumar contexto y detalles plausibles (historia general, logística típica, etc.),
   pero NO inventes datos sensibles ni verificables específicos (montos exactos, citas literales, acusaciones, nombres no mencionados).

9) NO uses "..." ni truncamientos. Nada de "Fecha:" ni línea de agencia tipo "—".

10) No menciones el medio original ni digas que es una reescritura.

11) TONO elegido: {tono_instr}

12) MODO NARRATIVO elegido (seguilo con precisión):
    {modo_instr}

13) IMPORTANTE: En el cuerpo, usá saltos de línea reales (\\n) entre párrafos y secciones.

FUENTE (para inspirarte, NO copiar):
TÍTULO ORIGINAL:
{titulo}

TEXTO ORIGINAL:
{cuerpo}

Recordá: Tu respuesta debe ser ÚNICAMENTE el objeto JSON, sin texto adicional."""


def procesar_con_gemini(*args, tono: str = "🧩 Realista", modo: str = "informativo") -> str:
    """
    Retrocompatible.
    Soporta:
      - procesar_con_gemini(texto, tono=..., modo=...)
      - procesar_con_gemini(titulo, cuerpo, tono=..., modo=...)
    Devuelve un string con JSON. El parser del bot se encarga de validarlo.

    IMPORTANTE: si Groq falla (modelo no encontrado, rate limit, timeout, etc.)
    esta función PROPAGA la excepción (raise) en vez de devolver un JSON falso
    "exitoso". Esto es a propósito: cron_base.py -> rewrite() ya tiene un
    try/except que captura esto, devuelve None, y el pipeline manda la alerta
    real de "❌ Error IA" por Telegram en vez de publicar una nota basura con
    apariencia de artículo válido.
    """
    # --- Interpretación de args (compatibilidad hacia atrás) ---
    if len(args) == 1:
        titulo = "Sin título"
        cuerpo = args[0] or ""
    elif len(args) >= 2:
        titulo = args[0] or "Sin título"
        cuerpo = args[1] or ""
    else:
        raise TypeError("procesar_con_gemini requiere (texto) o (titulo, cuerpo)")

    client = _get_client()

    # Modelo default actualizado: llama-3.3-70b-versatile fue deprecado por
    # Groq (anuncio 17/06/2026, baja efectiva desde entonces). Reemplazo
    # recomendado por Groq: openai/gpt-oss-120b (o qwen/qwen3.6-27b si se
    # prioriza velocidad sobre calidad). Se puede overridear con GROQ_MODEL.
    model = os.getenv("GROQ_MODEL", "").strip() or "openai/gpt-oss-120b"

    prompt = _build_prompt(titulo=titulo, cuerpo=cuerpo, tono=tono, modo=modo)

    # openai/gpt-oss-120b y openai/gpt-oss-20b son modelos "razonadores": antes
    # de escribir la respuesta final gastan tokens pensando, y esos tokens
    # salen del mismo presupuesto que max_tokens. Con reasoning_effort en su
    # default ("medium") y max_tokens bajo, se comía casi todo el presupuesto
    # pensando y dejaba el cuerpo del artículo truncado/cortísimo.
    # Solución: bajar el esfuerzo de razonamiento (no hace falta para reescribir
    # una noticia) y separar el razonamiento del contenido final con
    # reasoning_format="hidden" para que no se filtre mezclado en el texto.
    extra_kwargs = {}
    if model.startswith("openai/gpt-oss") or model.startswith("qwen/"):
        extra_kwargs["reasoning_effort"] = os.getenv("GROQ_REASONING_EFFORT", "low")
        extra_kwargs["reasoning_format"] = "hidden"

    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "system",
                    "content": "Sos un asistente experto en redacción periodística para medios digitales de Buenos Aires. Respondés ÚNICAMENTE en formato JSON válido, sin bloques markdown, sin texto adicional. Tu respuesta debe empezar con { y terminar con }.",
                },
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model=model,
            temperature=float(os.getenv("GROQ_TEMPERATURE", "0.9")),
            max_tokens=int(os.getenv("GROQ_MAX_TOKENS", "4096")),
            **extra_kwargs,
        )

        text = (chat_completion.choices[0].message.content or "").strip()

        print("\n=========== GROQ RAW RESPONSE (HEAD) ===========")
        print(text[:800])
        print("\n=========== GROQ RAW RESPONSE (TAIL) ===========")
        print(text[-400:] if len(text) > 400 else text)
        print("\n=========== END RESPONSE =========================")
        print(f"RAW LENGTH: {len(text)} chars | TONO: {tono} | MODO: {modo}")

        try:
            test_parse = json.loads(text)
            print(f"✅ JSON válido detectado. Keys: {list(test_parse.keys())}")
        except json.JSONDecodeError as e:
            print(f"⚠️ WARNING: La respuesta no parece ser JSON válido: {e}")
            print(f"Primeros 200 chars: {text[:200]}")

        return text

    except Exception as e:
        # ANTES: acá se devolvía un json.dumps({"titulo": "Error al procesar", ...})
        # que tenía forma de artículo válido y terminaba publicado en WordPress
        # como si fuera una noticia real (bug que generó los posts
        # "error-al-procesar-1" a "-6").
        # AHORA: se relanza la excepción para que el pipeline se entere del
        # fallo de verdad y no publique nada.
        print(f"❌ Error en procesar_con_gemini (Groq): {e}")
        raise


# Alias para compatibilidad si alguien importa con otro nombre
procesar_con_groq = procesar_con_gemini
