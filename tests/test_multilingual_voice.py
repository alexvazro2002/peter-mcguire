"""Spanish pronunciation with Peter's speaker identity and untouched captions."""
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.voice import VoiceController, utterance_id
from scripts import british_voice
from tests.test_speech_pipeline import wav


def model_files(tmp_path):
    model = tmp_path / 'model'
    for name in british_voice.REQUIRED_FILES + british_voice.SPANISH_FILES:
        path = model / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
    return model


def test_spanish_uses_spanish_phonemizer_with_the_same_george_speaker(tmp_path):
    model = model_files(tmp_path)
    seen = []
    class Engine:
        def generate(self, text, **kwargs):
            seen.append((text, kwargs))
            return SimpleNamespace(samples=[.1] * 2400, sample_rate=24000)
    def factory(path, *, language):
        assert path == model and language == 'es'
        return Engine()
    metadata = british_voice.synthesize(model, 'Hola, señor.', tmp_path / 'audio.wav',
                                        voice='bm_george', language='es', speed=1.15, engine_factory=factory)
    assert seen == [('Hola, señor.', dict(sid=26, speed=1.15))]
    assert metadata['language'] == metadata['phonemizer_voice'] == 'es'
    assert metadata['voice'] == 'bm_george' and metadata['speaker_id'] == 26


def test_mixed_sentences_keep_order_with_one_engine_per_language(tmp_path):
    model = model_files(tmp_path)
    manifest, output = tmp_path / 'segments.json', tmp_path / 'audio'
    output.mkdir()
    manifest.write_text(json.dumps(['Here is the email.',
        dict(text='Hola, señor.', language='es', voice='bm_george'), 'Shall I open it?']))
    loads, generated = [], []
    def factory(path, *, language='en'):
        loads.append(language)
        class Engine:
            def generate(self, text, **kwargs):
                generated.append((language, text, kwargs['sid']))
                return SimpleNamespace(samples=[.1] * 2400, sample_rate=24000)
        return Engine()
    british_voice.synthesize_segments(model, manifest, output, engine_factory=factory)
    assert loads == ['en', 'es']
    assert generated == [('en', 'Here is the email.', 26), ('es', 'Hola, señor.', 26),
                         ('en', 'Shall I open it?', 26)]
    assert len(list(output.glob('*.wav'))) == 3


def test_spanish_never_silently_uses_english_when_dictionary_missing(tmp_path):
    model = model_files(tmp_path)
    (model / british_voice.SPANISH_FILES[0]).unlink()
    with pytest.raises(british_voice.BritishVoiceError, match='Spanish phonemizer'):
        british_voice.validate_request(model, 'bm_george', 1.15, language='es')


def test_spanish_phonemizer_config_does_not_change_the_speaker_model(tmp_path):
    model = model_files(tmp_path)
    configurations = []
    def kokoro(**kwargs):
        configurations.append(kwargs)
        return kwargs
    sherpa = SimpleNamespace(OfflineTtsKokoroModelConfig=kokoro,
        OfflineTtsModelConfig=lambda **kwargs: kwargs,
        OfflineTtsConfig=lambda **kwargs: SimpleNamespace(validate=lambda: True),
        OfflineTts=lambda config: object())
    british_voice.create_engine(model, language='en', sherpa=sherpa)
    british_voice.create_engine(model, language='es', sherpa=sherpa)
    assert configurations[0]['lang'] == 'en' and configurations[1]['lang'] == 'es'
    assert {key: value for key, value in configurations[0].items() if key != 'lang'} == {
        key: value for key, value in configurations[1].items() if key != 'lang'}


def test_spanish_batch_language_can_keep_the_default_george_voice(tmp_path):
    model = model_files(tmp_path)
    manifest, output = tmp_path / 'segments.json', tmp_path / 'audio'
    output.mkdir()
    manifest.write_text(json.dumps(['Buenas tardes, señor.']))
    calls = []
    def factory(path, *, language):
        assert language == 'es'
        class Engine:
            def generate(self, text, **kwargs):
                calls.append(kwargs)
                return SimpleNamespace(samples=[.1] * 2400, sample_rate=24000)
        return Engine()
    british_voice.synthesize_segments(model, manifest, output, language='es', speed=1.15, engine_factory=factory)
    assert calls == [dict(sid=26, speed=1.15)]


def test_acknowledgement_finishes_before_queued_answer_without_cancellation():
    controller = VoiceController({})
    started, release, completed = threading.Event(), threading.Event(), threading.Event()
    seen = []
    controller._ready = lambda *args, **kwargs: None
    def speak(session, text, cancelled, *, captions=True):
        seen.append((text, captions, cancelled))
        if len(seen) == 1:
            started.set()
            assert release.wait(2)
        else:
            completed.set()
    controller._speak = speak
    try:
        controller.cancel()
        generation = controller._generation
        controller.speak('Give me a sec.', enqueue=True, captions=False)
        assert started.wait(2)
        controller.speak('Your task is saved.', enqueue=True)
        assert controller._generation == generation and not seen[0][2].is_set()
        release.set()
        assert completed.wait(2)
        assert [(text, captions) for text, captions, _ in seen] == [
            ('Give me a sec.', False), ('Your task is saved.', True)]
    finally:
        release.set()
        controller.close()


def test_cancel_discards_every_queued_utterance():
    controller = VoiceController({})
    started, release = threading.Event(), threading.Event()
    seen = []
    controller._ready = lambda *args, **kwargs: None
    def speak(session, text, cancelled, **kwargs):
        seen.append(text)
        started.set()
        release.wait(2)
    controller._speak = speak
    try:
        controller.speak('Give me a sec.', enqueue=True, captions=False)
        assert started.wait(2)
        controller.speak('Cancelled answer one.', enqueue=True)
        controller.speak('Cancelled answer two.', enqueue=True)
        controller.cancel()
        release.set()
        time.sleep(.15)
        assert seen == ['Give me a sec.'] and controller._pending.empty()
    finally:
        release.set()
        controller.close()


def test_progress_speech_has_no_caption_event(tmp_path):
    controller = VoiceController({})
    controller.sentence_pause = 0
    def generate(chunks, stop):
        for index, text in enumerate(chunks):
            wav(tmp_path / f'{index:04d}.wav')
    def play(args, cancelled, **kwargs):
        kwargs['on_start']()
    controller._run_audio = play
    try:
        controller._sentence_pipeline('Give me a minute.', tmp_path, generate, 'player',
                                      threading.Event(), captions=False)
        assert not any(event['type'] == 'voice_caption' for event in controller.events.queue)
    finally:
        controller.close()


def test_rendered_audio_keeps_original_caption_and_reply_identity(tmp_path):
    controller = VoiceController({})
    def generate(chunks, stop):
        assert chunks == ['10/10/2026 at 14:15.']
        wav(tmp_path / '0000.wav')
    controller._run_audio = lambda args, stop, **kwargs: kwargs['on_start']()
    original = '10/10/2026 at 14:15.'
    try:
        controller._sentence_pipeline(original, tmp_path, generate, 'player', threading.Event())
        captions = [event for event in controller.events.queue if event['type'] == 'voice_caption']
        assert len(captions) == 1
        assert captions[0]['text'] == original and captions[0]['utterance_id'] == utterance_id(original)
    finally:
        controller.close()


def test_native_pipeline_renders_dates_and_spanish_without_changing_captions(tmp_path):
    model = model_files(tmp_path)
    runtime = tmp_path / 'runtime'
    (runtime / '.venv/bin').mkdir(parents=True)
    (runtime / '.venv/bin/python').touch()
    controller = VoiceController(dict(voice_model='kokoro-onnx', voice_runtime=str(runtime),
                                     british_voice_path=str(model), voice_speed=1.15, voice_pitch=.8))
    original = '10/10/2026 at 14:15. Hola Alejandrito, Míralo y me dices.'
    manifests = []
    def run(argv, cancelled, **kwargs):
        if '--segments-file' in argv:
            manifest = json.loads(Path(argv[argv.index('--segments-file') + 1]).read_text())
            manifests.append(manifest)
            directory = Path(argv[argv.index('--output-dir') + 1])
            for index in range(len(manifest)):
                wav(directory / f'{index:04d}.wav')
            assert argv[argv.index('--voice') + 1] == 'bm_george'
            assert argv[argv.index('--speed') + 1] == '1.15'
        elif '-af' in argv:
            assert f'pitch={2 ** (.8 / 12):.8f}' in argv[argv.index('-af') + 1]
            Path(argv[-1]).write_bytes(Path(argv[argv.index('-i') + 1]).read_bytes())
        else:
            kwargs['on_start']()
    controller._run_audio = run
    controller.sentence_pause = 0
    try:
        controller._speak_british(original, 'player', threading.Event())
        assert manifests[0][0].startswith('October tenth') and 'quarter past two in the afternoon' in manifests[0][0]
        assert manifests[0][1] == dict(text='Hola Alejandrito, Míralo y me dices.', language='es', voice='bm_george')
        captions = [event for event in controller.events.queue if event['type'] == 'voice_caption']
        assert [event['text'] for event in captions] == [
            '10/10/2026 at 14:15.', 'Hola Alejandrito, Míralo y me dices.']
        assert all(event['utterance_id'] == utterance_id(original) for event in captions)
    finally:
        controller.close()


def test_visual_only_url_has_no_empty_tts_call(tmp_path):
    model = model_files(tmp_path)
    manifest, output = tmp_path / 'segments.json', tmp_path / 'audio'
    output.mkdir()
    manifest.write_text(json.dumps(['']))
    def factory(*args, **kwargs):
        raise AssertionError('A visual-only link must not load or call TTS')
    british_voice.synthesize_segments(model, manifest, output, engine_factory=factory)
    VoiceController._validate_wav(output / '0000.wav')
