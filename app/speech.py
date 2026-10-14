"""Small speech segments without splitting decimal numbers or common titles."""
import re


_ABBREVIATIONS = {"mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "vs", "etc", "e.g", "i.e", "a.m", "p.m"}


def sentences(text):
    text = str(text or "").strip()
    start, result = 0, []
    for boundary in re.finditer(r'[.!?]+[\"\u201d\u2019\)]*(?=\s|$)', text):
        token = text[start:boundary.start()].split()
        previous = token[-1].strip('"\u201c(').casefold() if token else ""
        if boundary.group().startswith('.') and (previous in _ABBREVIATIONS or
                re.fullmatch(r'(?:[a-z]\.)*[a-z]', previous)):
            continue
        value = text[start:boundary.end()].strip()
        if value:
            result.append(value)
        start = boundary.end()
    if text[start:].strip():
        result.append(text[start:].strip())
    return result


def speech_segments(text, limit=280):
    """A sentence per clip, with bounded phrases for unusually long sentences."""
    result = []
    for sentence in sentences(text):
        while len(sentence) > limit:
            boundary = max((m.end() for m in re.finditer(r'[,;:]\s+', sentence[:limit + 1])), default=0)
            if boundary < limit // 3:
                boundary = sentence.rfind(' ', 0, limit + 1)
            if boundary <= 0:
                boundary = limit
            result.append(sentence[:boundary].strip())
            sentence = sentence[boundary:].strip()
        if sentence:
            result.append(sentence)
    return result


def conversation_parts(messages):
    """Separate plain assistant sentences visually; keep actions on the last part."""
    for message in messages:
        text = message['text']
        # Lists, code and multiline email bodies retain their existing layout.
        parts = sentences(text) if message['role'] == 'assistant' and '\n' not in text and '`' not in text else [text]
        for index, part in enumerate(parts):
            if index == len(parts) - 1:
                yield dict(message, text=part)
            else:
                yield dict(role=message['role'], text=part)
