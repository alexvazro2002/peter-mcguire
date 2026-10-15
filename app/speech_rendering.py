"""Deterministic, voice-only rendering. Captions must retain the original text.

This is deliberately independent of the language model: a date, URL or bit of
Markdown should not need another inference before Peter can say it naturally.
"""
from datetime import date, datetime
from functools import lru_cache
import html
from pathlib import Path
import re
import unicodedata


_EN_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
              "September", "October", "November", "December")
_ES_MONTHS = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
              "septiembre", "octubre", "noviembre", "diciembre")
_ORDINALS = ("", "first", "second", "third", "fourth", "fifth", "sixth", "seventh",
             "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth", "fourteenth",
             "fifteenth", "sixteenth", "seventeenth", "eighteenth", "nineteenth", "twentieth",
             "twenty-first", "twenty-second", "twenty-third", "twenty-fourth", "twenty-fifth",
             "twenty-sixth", "twenty-seventh", "twenty-eighth", "twenty-ninth", "thirtieth",
             "thirty-first")
_EN_SMALL = ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
             "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
             "seventeen", "eighteen", "nineteen")
_ES_SMALL = ("cero", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve",
             "diez", "once", "doce", "trece", "catorce", "quince", "dieciséis", "diecisiete",
             "dieciocho", "diecinueve", "veinte", "veintiuno", "veintidós", "veintitrés",
             "veinticuatro", "veinticinco", "veintiséis", "veintisiete", "veintiocho", "veintinueve")
_ES_CLUES = set("el la los las un una unos unas del al que para por con sin pero como cuando donde "
                "es son y ver reservamos "
                "este esta estos estas ese esa esos esas hay hoy ayer mañana correo correos adjunto "
                "adjuntos asunto mensaje mensajes señor puedes puede quiero quieres tengo tienes "
                "tenemos hemos hola gracias buenas buenos tardes noches viaje vuelo vuelos hotel "
                "reserva reservas llegada salida muestrame dime leeme abrir leer enviar fecha "
                "año hora horas dia dias semana semanas mes meses febrero marzo abril mayo junio "
                "julio agosto septiembre octubre noviembre diciembre lunes martes miercoles jueves "
                "viernes sabado domingo mierc jue vie sab dom miralo dices llevar cabo posible borrador "
                "perfecto entendido claro".split())
_ES_STRONG = set("hola gracias señor correo correos adjunto adjuntos asunto buenos buenas tardes "
                 "noches mañana puedes quiero tienes tenemos llegada salida miralo dices leeme "
                 "muestrame borrador vuelo vuelos martes miercoles jueves viernes sabado domingo mierc "
                 "perfecto entendido".split())
_EN_CLUES = set("the a an this that these those is are was were be have has had my your our their "
                "to from for of with without and or but please would could should can you me it "
                "today tomorrow yesterday email emails attachment attachments subject message "
                "messages sir hello thanks thank good morning afternoon evening night read open "
                "reply flight flights hotel book booking arrival departure".split())


def _fold(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold())
                   if not unicodedata.combining(c))


_ES_CLUES = {_fold(word) for word in _ES_CLUES}
_ES_STRONG = {_fold(word) for word in _ES_STRONG}


@lru_cache(maxsize=1)
def _language_factory():
    """Load only the two offline profiles once; missing extras stay optional."""
    try:
        from langdetect.detector_factory import DetectorFactory, PROFILES_DIRECTORY
        from langdetect.lang_detect_exception import LangDetectException
    except ImportError:
        return None
    try:
        factory = DetectorFactory()
        factory.seed = 0
        directory = Path(PROFILES_DIRECTORY)
        factory.load_json_profile([(directory / language).read_text(encoding='utf-8')
                                   for language in ('en', 'es')])
        return factory
    except (OSError, ValueError, LangDetectException):
        return None


def _statistical_language(text):
    factory = _language_factory()
    if factory is None:
        return None
    from langdetect.lang_detect_exception import LangDetectException
    try:
        detector = factory.create()
        detector.append(text[:2000])
        candidates = detector.get_probabilities()
        best = candidates[0] if candidates else None
        return best.lang if best and best.prob >= .93 else None
    except LangDetectException:
        return None


def speech_language(text, default="en"):
    """Choose English or Spanish only when the words provide useful evidence.

    Names, file names, isolated dates and an English sentence mentioning Spain
    keep the configured language. Call this separately for each spoken sentence.
    """
    default = "es" if str(default).lower().startswith("es") else "en"
    # URL query parameters and email addresses provide no language evidence.
    text = re.sub(r"https?://\S+|\S+@\S+", ' ', str(text or ''))
    original_words = re.findall(r"[a-záéíóúüñ]+", text, re.I)
    words = [word.casefold() for word in original_words]
    folded = [_fold(word) for word in words]
    es = sum(word in _ES_CLUES for word in folded)
    en = sum(word in _EN_CLUES for word in folded)
    strong = sum(word in _ES_STRONG for word in folded)
    marks = bool(re.search(r"[¿¡ñ]", str(text or ""), re.I))
    if ((es >= 3 and es > en * 1.25) or (strong >= 1 and es >= 2 and es > en)
            or (marks and es >= 1 and es >= en) or (strong and len(words) <= 2 and not en)):
        return "es"
    if en >= 2 and en > es:
        return "en"
    # Names and short fragments are unreliable statistical input. Keep their
    # configured voice rather than letting, for example, José Ángel flip it.
    if (len(words) >= 4 and sum(map(len, words)) >= 20
            and not all(word[0].isupper() for word in original_words)):
        detected = _statistical_language(text)
        if detected:
            return detected
    return default


def _number(value, language):
    value = int(value)
    if value < 0:
        return ("menos " if language == "es" else "minus ") + _number(-value, language)
    if language == "es":
        if value < 30:
            return _ES_SMALL[value]
        if value < 100:
            tens = ("", "", "", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa")
            return tens[value // 10] + (" y " + _number(value % 10, language) if value % 10 else "")
        if value == 100:
            return "cien"
        if value < 1000:
            hundreds = ("", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos",
                        "seiscientos", "setecientos", "ochocientos", "novecientos")
            return hundreds[value // 100] + (" " + _number(value % 100, language) if value % 100 else "")
        if value < 10000:
            return ("mil" if value < 2000 else _number(value // 1000, language) + " mil") + (
                " " + _number(value % 1000, language) if value % 1000 else "")
    else:
        if value < 20:
            return _EN_SMALL[value]
        if value < 100:
            tens = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
            return tens[value // 10] + ("-" + _number(value % 10, language) if value % 10 else "")
        if value < 1000:
            return _number(value // 100, language) + " hundred" + (
                " and " + _number(value % 100, language) if value % 100 else "")
        if value < 10000:
            rest = value % 1000
            return _number(value // 1000, language) + " thousand" + (
                (" and " if rest < 100 else " ") + _number(rest, language) if rest else "")
    return str(value)


def _date_text(day, month, year, language, now):
    try:
        # A leap-year placeholder validates dates without an explicit year.
        date(year if year is not None else 2000, month, day)
    except (TypeError, ValueError):
        return None
    current_year = now.year if isinstance(now, (date, datetime)) else None
    year_text = "" if year is None or year == current_year else (
        " de " if language == "es" else ", ") + _number(year, language)
    if language == "es":
        return _number(day, language) + " de " + _ES_MONTHS[month - 1] + year_text
    return _EN_MONTHS[month - 1] + " " + _ORDINALS[day] + year_text


def _time_text(hour, minute, language, second=None):
    if not 0 <= hour <= 23 or not 0 <= minute <= 59 or (second is not None and not 0 <= second <= 59):
        return None
    if language == "es":
        spoken_hour = hour if minute <= 30 else (hour + 1) % 24
        twelve = spoken_hour % 12 or 12
        result = "la una" if twelve == 1 else "las " + _number(twelve, language)
        if minute == 15:
            result += " y cuarto"
        elif minute == 30:
            result += " y media"
        elif minute == 45:
            result += " menos cuarto"
        elif minute < 30 and minute:
            result += " y " + _number(minute, language)
        elif minute > 30:
            result += " menos " + _number(60 - minute, language)
        if minute == 0:
            result += " en punto"
        if second:
            result += " y " + _number(second, language) + " segundos"
        # Period refers to the actual instant, including 23:45 and 11:45.
        result += (" de la madrugada" if hour < 6 else " de la mañana" if hour < 12 else
                   " de la tarde" if hour < 20 else " de la noche")
        return result
    if minute == 0 and not second and hour in (0, 12):
        return "midnight" if hour == 0 else "noon"
    target = hour if minute <= 30 else (hour + 1) % 24
    spoken_hour = _number(target % 12 or 12, language)
    if minute == 15:
        result = "quarter past " + spoken_hour
    elif minute == 30:
        result = "half past " + spoken_hour
    elif minute == 45:
        result = "quarter to " + spoken_hour
    elif minute == 0:
        result = spoken_hour + " o'clock"
    elif minute < 30:
        result = _number(minute, language) + " past " + spoken_hour
    else:
        result = _number(60 - minute, language) + " to " + spoken_hour
    if second:
        result += " and " + _number(second, language) + (" second" if second == 1 else " seconds")
    return result + (" at night" if hour < 6 or hour >= 22 else " in the morning" if hour < 12 else
                     " in the afternoon" if hour < 18 else " in the evening")


def render_speech(text, language="en", now=None):
    """Return natural TTS input without changing the displayed reply.

    Numeric slash dates use Peter's British day/month convention. Invalid dates
    and times remain intact rather than being silently reinterpreted. Supplying
    ``now`` allows this year's date to be spoken without its redundant year.
    ``language`` is explicit so detection and voice selection can share it.
    """
    language = "es" if str(language).lower().startswith("es") else "en"
    text = html.unescape(str(text or ""))
    text = re.sub(r"```[\s\S]*?```", " ", text)
    text = re.sub(r"!\[([^\]]*)\]\([^\n)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^\n)]*\)", r"\1", text)
    # Do not say opaque message IDs, query strings or authentication URL tokens.
    text = re.sub(r"https?://[^\s<>]+", lambda m: "." if m.group().endswith('.') else " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"(?m)^\s*(?:#{1,6}\s*|[-*•▪]+\s+|\d+[.)]\s+|>\s*)", "", text)
    text = re.sub(r"[`*_~]", "", text)

    def numeric_date(match):
        day, month, year = match.group('d'), match.group('m'), match.group('y')
        result = _date_text(int(day), int(month), int(year) if year else None, language, now)
        return result or match.group()

    def iso_date(match):
        result = _date_text(int(match['d']), int(match['m']), int(match['y']), language, now)
        return result or match.group()

    text = re.sub(r"(?<![\w/])(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})(?![\d-])", iso_date, text)
    text = re.sub(r"(?<![\w/])(?P<d>\d{1,2})/(?P<m>\d{1,2})(?:/(?P<y>\d{4}))?(?![\d/])", numeric_date, text)
    month_names = {name.casefold(): i + 1 for i, name in enumerate(_EN_MONTHS)}
    month_names.update({name[:3].casefold(): i + 1 for i, name in enumerate(_EN_MONTHS)})
    month_names.update({name.casefold(): i + 1 for i, name in enumerate(_ES_MONTHS)})
    month_names.update({name[:3].casefold(): i + 1 for i, name in enumerate(_ES_MONTHS)})
    def named_date(match):
        month = month_names.get(match['m'].casefold())
        if not month:
            return match.group()
        result = _date_text(int(match['d']), month, int(match['y']) if match['y'] else None, language, now)
        return result or match.group()
    text = re.sub(r"(?<![\w/])(?P<d>\d{1,2})/(?P<m>[A-Za-záéíóúñ]+)(?:/(?P<y>\d{4}))?(?![\w/])", named_date, text)

    def time_text(match):
        hour, minute = int(match['h']), int(match['m'] or 0)
        period = re.sub(r"[.\s]", "", match['p'] or '').casefold()
        if period:
            if not 1 <= hour <= 12:
                return match.group()
            hour = hour % 12 + (12 if period == 'pm' else 0)
        result = _time_text(hour, minute, language, int(match['s']) if match['s'] else None)
        return result or match.group()
    time_pattern = (r"(?<![\w/:])(?P<h>\d{1,2})(?:[:](?P<m>\d{2})(?::(?P<s>\d{2}))?)?"
                    r"\s*(?P<p>[ap]\.?m\.?)?(?![\w/:])")
    # Bare numbers are not times. Require either a colon or an am/pm marker.
    def maybe_time(match):
        return time_text(match) if match['m'] is not None or match['p'] else match.group()
    text = re.sub(time_pattern, maybe_time, text, flags=re.I)

    def money(match):
        symbol, amount = match['symbol'], match['amount']
        # English comma grouping is supported. Spanish decimal commas are kept
        # when there is no thousands-group shape or decimal point.
        if language == 'es' and ',' in amount and '.' not in amount and not re.fullmatch(r"\d{1,3}(?:,\d{3})+", amount):
            amount = amount.replace(',', '.')
        else:
            amount = amount.replace(',', '')
        whole, _, fraction = amount.partition('.')
        if len(fraction) > 2 or len(whole) > 4:
            return match.group()
        major, minor = int(whole), int((fraction + '00')[:2])
        units = {'£': ('pound', 'pounds', 'penny', 'pence'), '$': ('dollar', 'dollars', 'cent', 'cents'),
                 '€': ('euro', 'euros', 'cent', 'cents')}
        if language == 'es':
            units = {'£': ('libra', 'libras', 'penique', 'peniques'), '$': ('dólar', 'dólares', 'centavo', 'centavos'),
                     '€': ('euro', 'euros', 'céntimo', 'céntimos')}
        one, many, small_one, small_many = units[symbol]
        result = _number(major, language) + ' ' + (one if major == 1 else many)
        if minor:
            result += (' con ' if language == 'es' else ' and ') + _number(minor, language) + ' ' + (small_one if minor == 1 else small_many)
        return result
    text = re.sub(r"(?P<symbol>[£$€])\s*(?P<amount>\d[\d,]*(?:\.\d+)?)(?!\w)", money, text)
    and_word, or_word, to_word = ('y', 'o', 'a') if language == 'es' else ('and', 'or', 'to')
    text = re.sub(r"[➔➜→⟶]", ' ' + to_word + ' ', text)
    text = re.sub(r"\s+[–—-]\s+", ', ', text)
    text = text.replace('&', ' ' + and_word + ' ')
    text = re.sub(r"\b(?:and/or|y/o)\b", and_word + ' ' + or_word, text, flags=re.I)
    text = re.sub(r"\s*/\s*(?=\s|$)|\s+/\s*|(?<=[A-Za-zÀ-ÿ])/(?=[A-Za-zÀ-ÿ])", ' ' + or_word + ' ', text)
    text = re.sub(r"(?<=\d)%", ' por ciento' if language == 'es' else ' percent', text)
    text = re.sub(r"[\[\]{}|]", ' ', text)
    # Leave malformed numeric clocks intact rather than altering their value.
    text = re.sub(r"(?<!\d):|:(?!\d)|;", ', ', text)
    if language == 'es':
        # The source often already says "a las 14:15".
        text = re.sub(r"\b(?:la|las)\s+(la|las)\b", r"\1", text, flags=re.I)
    text = re.sub(r"\s+([,.!?])", r"\1", text)
    text = re.sub(r",\s*,+", ',', text)
    text = re.sub(r",\s*([.!?])", r"\1", text)
    text = re.sub(r"\s+", ' ', text).strip(' ,')
    return text
