from core.fact_extractor import extract_fact
from core.tone_manager import get_tone
from core.ai_writer import generate_article
from core.validator import validate_article

def run_notibot(client, original_text, tone_key="serio"):
    hecho = extract_fact(original_text)
    tono = get_tone(tone_key)
    article = generate_article(client, hecho, original_text, tono)

    is_valid, message = validate_article(article)

    if not is_valid:
        raise Exception(f"Validation failed: {message}")

    return article