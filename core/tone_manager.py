TONOS = {
    "serio": "Tono formal, profesional y analítico.",
    "entusiasta": "Tono dinámico, enérgico y optimista.",
    "realista": "Tono equilibrado y reflexivo.",
    "clasico": "Estilo periodístico tradicional."
}

def get_tone(tone_key):
    return TONOS.get(tone_key, TONOS["serio"])