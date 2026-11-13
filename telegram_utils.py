
# telegram_utils.py
from typing import List
from telegram import Update
from telegram.ext import ContextTypes
import re

MAX_CAPTION_LEN = 1024
MAX_MSG_LEN = 4096

def split_text(text: str, max_len: int = MAX_MSG_LEN) -> List[str]:
    text = text.strip()
    if len(text) <= max_len:
        return [text]
    parts: List[str] = []
    buffer = ""

    def flush():
        nonlocal buffer
        if buffer.strip():
            parts.append(buffer.strip())
        buffer = ""

    paragraphs = text.split("\n\n")
    for p in paragraphs:
        p = p.strip()
        if len(buffer) + len(p) + 2 <= max_len:
            buffer = (buffer + "\n\n" + p).strip() if buffer else p
        else:
            sentences = re.split(r"(?<=[.!?])\s+", p)
            for s in sentences:
                if len(s) > max_len:
                    for i in range(0, len(s), max_len):
                        chunk = s[i:i+max_len]
                        if len(buffer) + len(chunk) + 1 <= max_len:
                            buffer = (buffer + " " + chunk).strip() if buffer else chunk
                        else:
                            flush()
                            buffer = chunk
                else:
                    if len(buffer) + len(s) + 1 <= max_len:
                        buffer = (buffer + " " + s).strip() if buffer else s
                    else:
                        flush()
                        buffer = s
            flush()
    flush()
    return parts

async def send_full_post(update: Update, context: ContextTypes.DEFAULT_TYPE, titulo: str, cuerpo: str, imagen_url: str | None = None, parse_mode: str | None = "Markdown"):
    chat = update.effective_chat
    title_md = f"*{titulo}*" if titulo else "*(sin título)*"
    body = cuerpo or "(sin cuerpo)"
    if imagen_url:
        espacio_restante = MAX_CAPTION_LEN - len(title_md) - 2
        cuerpo_caption = body[:espacio_restante] if espacio_restante > 0 else ""
        caption = (title_md + "\n\n" + cuerpo_caption).strip()
        await context.bot.send_photo(chat_id=chat.id, photo=imagen_url, caption=caption, parse_mode=parse_mode)
        resto = body[len(cuerpo_caption):].strip()
        if resto:
            for chunk in split_text(resto, MAX_MSG_LEN):
                await context.bot.send_message(chat_id=chat.id, text=chunk, parse_mode=parse_mode)
    else:
        full = f"{title_md}\n\n{body}".strip()
        for chunk in split_text(full, MAX_MSG_LEN):
            await context.bot.send_message(chat_id=chat.id, text=chunk, parse_mode=parse_mode)
