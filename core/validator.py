def count_words(text):
    return len(text.split())

def validate_article(text):
    word_count = count_words(text)

    if word_count < 600:
        return False, "Artículo demasiado corto"

    forbidden_phrases = [
        "según el medio",
        "como informó",
        "en el artículo original"
    ]

    for phrase in forbidden_phrases:
        if phrase in text.lower():
            return False, "Referencia indebida detectada"

    return True, "OK"