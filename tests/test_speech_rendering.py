from datetime import date, datetime

import pytest

from app.speech_rendering import render_speech, speech_language


def test_voice_date_and_time_leave_the_original_caption_intact():
    caption = '10/10/2026 at 14:15.'
    assert render_speech(caption, now=date(2026, 10, 15)) == 'October tenth at quarter past two in the afternoon.'
    assert caption == '10/10/2026 at 14:15.'


def test_date_year_is_omitted_only_when_known_to_be_the_current_year():
    assert render_speech('10/10/2026', now=datetime(2027, 1, 1)) == 'October tenth, two thousand and twenty-six'
    assert render_speech('10/10/2026') == 'October tenth, two thousand and twenty-six'
    assert render_speech('10/10') == 'October tenth'
    assert render_speech('2026-10-10', now=date(2026, 1, 1)) == 'October tenth'


@pytest.mark.parametrize('written,spoken', [
    ('09:00', "nine o'clock in the morning"),
    ('8pm', "eight o'clock in the evening"),
    ('8:15 p.m.', 'quarter past eight in the evening'),
    ('14:30', 'half past two in the afternoon'),
    ('14:45', 'quarter to three in the afternoon'),
    ('02:05', 'five past two at night'),
    ('12:00', 'noon'),
    ('00:00', 'midnight'),
])
def test_natural_english_clock(written, spoken):
    assert render_speech(written) == spoken


def test_dates_are_british_and_invalid_values_are_not_reinterpreted():
    assert render_speech('04/05/2026', now=date(2026, 1, 1)) == 'May fourth'
    assert render_speech('29/02/2024', now=date(2024, 1, 1)) == 'February twenty-ninth'
    assert render_speech('31/02/2026 and 25:70') == '31/02/2026 and 25:70'
    assert render_speech('29/02/2025') == '29/02/2025'


def test_spanish_dates_and_clock_are_not_anglicised():
    assert render_speech('El 10/10/2026 a las 14:15.', 'es', date(2026, 10, 15)) == (
        'El diez de octubre a las dos y cuarto de la tarde.')
    assert render_speech('9/feb a 02:50', 'es') == 'nueve de febrero a las tres menos diez de la madrugada'
    assert render_speech('2027-03-01', 'es', date(2026, 10, 15)) == 'uno de marzo de dos mil veintisiete'


def test_markup_links_and_arrows_are_only_turned_into_speech_words():
    written = '**Flight:** EDI 12:00 ➔ SAW 19:20. [Open email](https://mail.google.com/mail/u/0/#inbox/private-id).'
    spoken = render_speech(written)
    assert spoken == 'Flight, EDI noon to SAW twenty past seven in the evening. Open email.'
    assert 'private-id' not in spoken and '#inbox' not in spoken
    assert '**Flight:**' in written
    assert render_speech('**Hello** [Alex](https://example.com).\n```python\nsecret\n```') == 'Hello Alex.'
    assert render_speech('See https://example.com/private?id=23.') == 'See.'
    assert render_speech('- First item\n- Second item') == 'First item Second item'


def test_numbers_that_are_not_times_and_decimal_amounts_remain_meaningful():
    assert render_speech('There are 3 emails and the ratio is 3.14.') == 'There are 3 emails and the ratio is 3.14.'
    assert render_speech('The fee is £24.50 & the deposit is €1,200.') == (
        'The fee is twenty-four pounds and fifty pence and the deposit is one thousand two hundred euros.')
    assert render_speech('Cuesta €24,50.', 'es') == 'Cuesta veinticuatro euros con cincuenta céntimos.'
    assert render_speech('The total is 25%.') == 'The total is 25 percent.'


@pytest.mark.parametrize('text,language', [
    ('Hola Alejandrito, míralo y me dices.', 'es'),
    ('Hola Alejandrito.', 'es'),
    ('Gracias.', 'es'),
    ('A ver qué hotel es y reservamos. Mañana tenemos la llegada a Madrid.', 'es'),
    ('A ver qué hotel es y reservamos.', 'es'),
    ('¿Quieres que abra el correo, señor?', 'es'),
    ('Los vuelos son el martes, con salida desde Edimburgo.', 'es'),
    ('o Martes 9/feb: Vuelo EDI 12:00 ➔ SAW (Estambul) 19:20.', 'es'),
    ('This email from José Ángel has a Spanish subject.', 'en'),
    ('Would you like me to open the email for you, sir?', 'en'),
    ('October 10th at quarter past two in the afternoon.', 'en'),
])
def test_language_detection_uses_words_rather_than_the_sender_name(text, language):
    assert speech_language(text) == language


def test_uncertain_text_uses_the_configured_language():
    assert speech_language('José Ángel') == 'en'
    assert speech_language('14:15', default='es-ES') == 'es'
    assert speech_language('This is the next message.', default='es') == 'en'
    assert render_speech(None) == ''


def test_word_slashes_are_spoken_as_a_choice_without_altering_invalid_dates():
    assert render_speech('Choose today/tomorrow and/or Friday.') == 'Choose today or tomorrow and or Friday.'
    assert render_speech('Elige hoy/mañana.', 'es') == 'Elige hoy o mañana.'
    assert render_speech('The incorrect date was 31/02/2026.') == 'The incorrect date was 31/02/2026.'


@pytest.mark.parametrize('text,expected', [
    ('Convendría aplazar la reunión mientras comprobamos disponibilidad y alojamiento.', 'es'),
    ('Recibiste documentación complementaria sobre aquellas gestiones iniciadas recientemente.', 'es'),
    ('Organizaríamos actividades culturales durante aquellas maravillosas vacaciones primaverales.', 'es'),
    ('These arrangements may require considerable preparation before beginning.', 'en'),
    ('Unfortunately additional documentation remains unavailable pending further investigation.', 'en'),
    ('Alejandro Vázquez and José Ángel arranged these discussions quite efficiently.', 'en'),
])
def test_arbitrary_sentences_can_use_offline_statistical_language_detection(text, expected):
    pytest.importorskip('langdetect')
    assert speech_language(text) == expected


def test_statistical_profiles_are_cached_restricted_and_deterministic():
    pytest.importorskip('langdetect')
    from app.speech_rendering import _language_factory
    factory = _language_factory()
    assert factory is not None and set(factory.get_lang_list()) == {'en', 'es'}
    assert _language_factory() is factory
    text = 'Recibiste documentación complementaria sobre aquellas gestiones iniciadas recientemente.'
    assert [speech_language(text) for _ in range(5)] == ['es'] * 5


def test_names_numbers_and_links_do_not_trigger_statistical_language_changes():
    assert speech_language('José Ángel Manuel Alejandro Vázquez') == 'en'
    assert speech_language('10/10/2026 14:15 £24.50') == 'en'
    assert speech_language('José Ángel https://example.es/correo/para/abrir/hoy') == 'en'
    assert speech_language('https://example.com/this/is/an/english/link', default='es') == 'es'


def test_missing_optional_language_package_keeps_short_phrase_detection(monkeypatch):
    import app.speech_rendering as speech
    monkeypatch.setattr(speech, '_language_factory', lambda: None)
    assert speech_language('A ver qué hotel es y reservamos.') == 'es'
    assert speech_language('Organizaríamos actividades culturales durante aquellas vacaciones primaverales.') == 'en'
