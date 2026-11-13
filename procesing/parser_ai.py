# procesing/parser_ai.py
import json
import re
import logging
from typing import Dict, Any

# Configurar logger
log = logging.getLogger("notibot")


def _clean_spaces(s: str) -> str:
    """Limpia espacios extra manteniendo la estructura del texto."""
    if not s:
        return ""
    s = s.replace("\r", "")
    s = re.sub(r"[ \t]+", " ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return s.strip()


def _strip_labels(s: str) -> str:
    """Remueve etiquetas tipo 'Título:', 'Cuerpo:', etc. del inicio de líneas."""
    if not s:
        return s
    pattern = r"^\s*(t[ií]tulo|title|cuerpo|contenido|body|imagen|image|imagen_url|image_url)\s*:\s*"
    lines = s.splitlines()
    new_lines = []
    for line in lines:
        new_lines.append(re.sub(pattern, "", line, flags=re.IGNORECASE))
    return "\n".join(new_lines).strip()


def _dedupe_title_from_body(title: str, body: str) -> str:
    """Elimina el título del inicio del cuerpo si está duplicado."""
    if not title or not body:
        return body or ""
    body_stripped = body.lstrip()
    first_line = body_stripped.split("\n", 1)[0].strip()
    candidates = {
        title.strip(),
        f"Título: {title.strip()}",
        f"Titulo: {title.strip()}",
        f"Title: {title.strip()}",
        f"# {title.strip()}",
        f"**{title.strip()}**",
    }
    if first_line in candidates or first_line.lower() == title.strip().lower():
        rest = body_stripped.split("\n", 1)
        return rest[1].strip() if len(rest) > 1 else ""
    return body


def _cleanup_title(title: str) -> str:
    """Limpia el título de basura común."""
    t = (title or "").strip()
    # sacar basura típica: "Fecha de publicación: ..."
    t = re.sub(r"(?i)\b(fecha de publicaci[óo]n|publicado|fecha)\s*:\s*.*$", "", t).strip()
    # cortar si viene pegada una fecha al final
    t = re.sub(r"\s*\|\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}.*$", "", t).strip()
    # evitar punto final en título
    t = t[:-1] if t.endswith(".") else t
    # hard cap amigable
    if len(t) > 120:
        t = t[:117].rstrip() + "…"
    return t


def _remove_metadata_lines(body: str) -> str:
    """Elimina líneas tipo dateline ("Ciudad — fecha") y otras cabeceras vacías."""
    if not body:
        return ""
    lines = [ln.strip() for ln in body.replace("\r", "").split("\n")]
    cleaned = []
    for ln in lines:
        if not ln:
            cleaned.append("")
            continue
        # dateline con guión largo
        if "—" in ln and re.search(r"\b\d{4}\b", ln):
            continue
        # dateline con fecha en español
        if re.search(r"\b\d{1,2}\s+de\s+[a-záéíóúñ]+\s+de\s+\d{4}\b", ln, flags=re.IGNORECASE):
            # si además parece encabezado corto, lo sacamos
            if len(ln) < 80:
                continue
        cleaned.append(ln)
    # normalizar saltos múltiples
    out = "\n".join(cleaned)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


def _convertir_markdown_a_html(texto: str) -> str:
    """
    Convierte Markdown básico a HTML para WordPress:
    - ## Título → <h2>Título</h2>
    - # Título → <h1>Título</h1> (si aparece)
    - **texto** → <strong>texto</strong>
    - *texto* → <em>texto</em>
    """
    if not texto:
        return texto
    
    log.info(f"=== CONVIRTIENDO MARKDOWN A HTML ===")
    log.info(f"Texto original (primeros 200 chars): {texto[:200]}")
    
    # Contar cuántos ## hay
    count_h2 = len(re.findall(r'^##\s+', texto, flags=re.MULTILINE))
    log.info(f"Encontrados {count_h2} subtítulos ##")
    
    # Convertir ## Título (H2) - más permisivo: ## seguido de espacios y texto
    texto = re.sub(r'^##\s+(.+)$', r'<h2>\1</h2>', texto, flags=re.MULTILINE)
    
    # Convertir # Título (H1) - solo si no es ## (ya convertido)
    texto = re.sub(r'^#\s+(.+)$', r'<h1>\1</h1>', texto, flags=re.MULTILINE)
    
    # Convertir **negrita** → <strong>
    texto = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', texto)
    
    # Convertir *cursiva* → <em>
    texto = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<em>\1</em>', texto)
    
    log.info(f"Texto convertido (primeros 200 chars): {texto[:200]}")
    log.info(f"=====================================")
    
    return texto


def _extract_json_from_markdown(raw: str) -> str:
    """
    Extrae JSON de un bloque de código markdown ```json o ```
    Devuelve el string JSON limpio, o el raw original si no hay bloque.
    """
    # Buscar bloque ```json ... ``` o ``` ... ```
    match = re.search(r'```(?:json)?\s*(\{[\s\S]*?\})\s*```', raw, re.IGNORECASE | re.DOTALL)
    if match:
        json_str = match.group(1)
        log.info("✓ Encontró bloque ```json, extrayendo...")
        return json_str.strip()
    
    return raw.strip()


def _parse_json_safely(text: str) -> Dict[str, Any]:
    """
    Parsea JSON de forma segura, manejando múltiples casos:
    1. JSON limpio directo
    2. JSON dentro de bloques markdown ```json
    3. JSON con texto extra antes/después
    """
    text = text.strip()
    
    log.info("=" * 60)
    log.info("INTENTANDO PARSEAR JSON")
    log.info(f"Primeros 300 chars: {text[:300]}")
    log.info(f"Últimos 100 chars: {text[-100:]}")
    
    # Paso 1: Intentar extraer de markdown si tiene ```
    if "```" in text:
        text = _extract_json_from_markdown(text)
    
    # Paso 2: Buscar el objeto JSON principal si hay texto extra
    if not text.startswith('{'):
        # Buscar el primer { y extraer desde ahí
        start = text.find('{')
        if start != -1:
            text = text[start:]
            log.info(f"✓ Encontró {{ en posición {start}, extrayendo desde ahí")
    
    # Paso 3: Encontrar el cierre correcto del JSON contando llaves
    if text.startswith('{'):
        brace_count = 0
        in_string = False
        escape_next = False
        end_pos = -1
        
        for i, char in enumerate(text):
            if escape_next:
                escape_next = False
                continue
                
            if char == '\\':
                escape_next = True
                continue
                
            if char == '"' and not escape_next:
                in_string = not in_string
                continue
                
            if not in_string:
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = i
                        break
        
        if end_pos != -1:
            text = text[:end_pos + 1]
            log.info(f"✓ JSON completo encontrado hasta posición {end_pos}")
    
    # Paso 4: Intentar parsear
    try:
        data = json.loads(text)
        log.info(f"✅ JSON parseado exitosamente. Keys: {list(data.keys())}")
        return data
    except json.JSONDecodeError as e:
        log.error(f"❌ Error parseando JSON: {e}")
        log.error(f"Texto que falló: {text[:500]}")
        return {}


def _finalize(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Normaliza salida: limpia título/cuerpo, elimina metadatos, dedupe y convierte Markdown a HTML."""
    
    # Extraer valores con diferentes keys posibles
    titulo = parsed.get("titulo") or parsed.get("title") or ""
    cuerpo = parsed.get("cuerpo") or parsed.get("body") or parsed.get("contenido") or ""
    imagen_url = parsed.get("imagen_url") or parsed.get("image_url") or parsed.get("image") or ""
    tags = parsed.get("tags", []) or []
    
    # Limpiar
    titulo = _cleanup_title(_clean_spaces(_strip_labels(titulo)))
    cuerpo = _clean_spaces(_strip_labels(cuerpo))
    cuerpo = _remove_metadata_lines(cuerpo)
    cuerpo = _dedupe_title_from_body(titulo, cuerpo)
    
    # Convertir Markdown a HTML (solo si el cuerpo es texto válido)
    if cuerpo and len(cuerpo) > 10:
        cuerpo = _convertir_markdown_a_html(cuerpo)
    
    imagen_url = _clean_spaces(_strip_labels(imagen_url))
    
    return {
        "titulo": titulo,
        "cuerpo": cuerpo,
        "imagen_url": imagen_url if imagen_url else None,
        "tags": tags if isinstance(tags, list) else []
    }


def parse_ai_output(texto: str) -> Dict[str, Any]:
    """
    Parsea la salida de la IA (Groq/Gemini) y extrae título, cuerpo, imagen_url y tags.
    
    NUEVO ENFOQUE (2024):
    - Parsea el JSON directamente en lugar de usar regex
    - Maneja bloques markdown ```json
    - Fallback robusto si algo falla
    """
    raw = (texto or "").strip()
    
    log.info("=" * 60)
    log.info("PARSE_AI_OUTPUT - VERSIÓN JSON PARSER")
    log.info(f"Input length: {len(raw)} chars")
    
    if not raw:
        log.warning("⚠️ Input vacío")
        return {"titulo": "", "cuerpo": "", "imagen_url": None, "tags": []}
    
    # Intentar parsear como JSON
    parsed = _parse_json_safely(raw)
    
    if parsed and (parsed.get("titulo") or parsed.get("cuerpo") or parsed.get("title") or parsed.get("body")):
        log.info("✅ Parseo JSON exitoso")
        result = _finalize(parsed)
        log.info(f"Título final: {result['titulo'][:100]}")
        log.info(f"Cuerpo length: {len(result['cuerpo'])} chars")
        log.info(f"Tags: {result['tags']}")
        log.info("=" * 60)
        return result
    
    # FALLBACK: Si el JSON no funciona, intentar regex (legacy)
    log.warning("⚠️ JSON parsing falló, intentando fallback con regex...")
    
    titulo = ""
    cuerpo = ""
    
    # Buscar título
    titulo_match = re.search(r'"(?:titulo|title)"\s*:\s*"([^"]+)"', raw, re.IGNORECASE)
    if titulo_match:
        titulo = titulo_match.group(1).strip()
        log.info(f"✓ Título encontrado con regex: {titulo[:100]}")
    
    # Buscar cuerpo - más complejo por los saltos de línea
    cuerpo_match = re.search(r'"(?:cuerpo|body|contenido)"\s*:\s*"((?:[^"\\]|\\.)*)"', raw, re.IGNORECASE | re.DOTALL)
    if cuerpo_match:
        cuerpo = cuerpo_match.group(1).strip()
        # Deshacer escapes JSON
        cuerpo = cuerpo.replace('\\n', '\n').replace('\\"', '"').replace('\\t', '\t').replace('\\\\', '\\')
        log.info(f"✓ Cuerpo encontrado con regex, length: {len(cuerpo)}")
    
    if titulo or cuerpo:
        parsed = {
            "titulo": titulo,
            "cuerpo": cuerpo,
            "imagen_url": None,
            "tags": []
        }
        result = _finalize(parsed)
        log.info("✓ Fallback regex exitoso")
        log.info("=" * 60)
        return result
    
    # FALLBACK FINAL: devolver vacío
    log.error("❌ No se pudo extraer nada del texto")
    log.info("=" * 60)
    return {"titulo": "", "cuerpo": "", "imagen_url": None, "tags": []}
