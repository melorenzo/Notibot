def extract_fact(text):
    sentences = text.split(".")
    return sentences[0].strip()