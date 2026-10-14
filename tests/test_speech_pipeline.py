import json
import threading
import time
import wave
from array import array
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.speech import conversation_parts, sentences, speech_segments
from app.voice import VoiceController, VoiceError, _Cancelled
from scripts.british_voice import synthesize_segments


def wav(path):
    partial = path.with_suffix('.partial')
    with wave.open(str(partial), 'wb') as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(24000)
        output.writeframes(array('h', [1000] * 2400).tobytes())
    partial.replace(path)


@pytest.mark.parametrize('text, expected', [
    ('Great choice, sir. You have 3 new messages. Do you want me to read them?',
     ['Great choice, sir.', 'You have 3 new messages.', 'Do you want me to read them?']),
    ('Dr. Smith arrives at 9.30 a.m. today. Ready?', ['Dr. Smith arrives at 9.30 a.m. today.', 'Ready?']),
    ('"Yes!" That is ready.', ['"Yes!"', 'That is ready.']),
    ('The value is 3.14. Visit example.com. Next sentence.', ['The value is 3.14.', 'Visit example.com.', 'Next sentence.']),
])
def test_sentence_boundaries(text, expected):
    assert sentences(text) == expected
    assert speech_segments(text) == expected


def test_long_sentences_preserve_all_words_in_bounded_clips():
    text = ' '.join(f'word{i}' for i in range(250)) + '.'
    parts = speech_segments(text)
    assert max(map(len, parts)) <= 280
    assert ' '.join(parts) == text
    unbroken = 'x' * 600
    assert ''.join(speech_segments(unbroken)) == unbroken


def test_three_visual_messages_keep_original_sources_and_undo_on_last():
    message = dict(role='assistant', text='Great choice, sir. You have 3 new messages. Shall I read them?',
                   sources=[dict(kind='email', title='Mail')], operation_id='change1')
    parts = list(conversation_parts([message]))
    assert len(parts) == 3 and parts[-1]['sources'] == message['sources']
    assert parts[-1]['operation_id'] == 'change1'
    assert all('sources' not in part and 'operation_id' not in part for part in parts[:-1])
    assert message['text'].startswith('Great choice')
    assert len(list(conversation_parts([dict(role='user', text=message['text'])]))) == 1


def test_next_sentence_generates_during_playback_and_waits_three_tenths(tmp_path):
    controller = VoiceController({})
    second_ready, playing, release = threading.Event(), threading.Event(), threading.Event()
    sequence, starts, ends = [], [], []
    def produce(chunks, cancel):
        wav(tmp_path / '0000.wav')
        assert playing.wait(2)
        wav(tmp_path / '0001.wav')
        second_ready.set()
        wav(tmp_path / '0002.wav')
    def play(argv, cancel, **kwargs):
        sequence.append(Path(argv[-1]).name)
        starts.append(time.monotonic())
        if len(sequence) == 1:
            playing.set()
            assert second_ready.wait(2), 'Synthesis did not overlap playback'
        ends.append(time.monotonic())
    controller._run_audio = play
    try:
        controller._sentence_pipeline('One sentence. Second sentence. Third sentence.', tmp_path, produce,
                                      '/usr/bin/pw-play', threading.Event())
        assert sequence == ['0000.wav', '0001.wav', '0002.wav']
        assert all(.28 <= starts[i] - ends[i - 1] < .5 for i in range(1, 3))
        assert not list(tmp_path.glob('*.wav'))
    finally:
        controller.close()


def test_producer_stays_at_most_three_unfinished_clips_ahead(tmp_path):
    controller = VoiceController({})
    observed = []
    def produce(chunks, stop):
        for i, chunk in enumerate(chunks):
            controller._wait_for_space(tmp_path, i, stop)
            wav(tmp_path / f'{i:04d}.wav')
            observed.append(i)
    def play(argv, cancel, **kwargs):
        if not (tmp_path / 'consumed-0').exists():
            time.sleep(.12)
            assert observed == [0, 1, 2]
    controller._run_audio = play
    controller.sentence_pause = 0
    try:
        controller._sentence_pipeline('One. Two. Three. Four. Five.', tmp_path, produce, 'player', threading.Event())
        assert observed == [0, 1, 2, 3, 4]
    finally:
        controller.close()


def test_cancel_during_gap_discards_remaining_sentences(tmp_path):
    controller, cancelled = VoiceController({}), threading.Event()
    plays = []
    def produce(chunks, stop):
        for i in range(len(chunks)):
            wav(tmp_path / f'{i:04d}.wav')
    def play(argv, cancel, **kwargs):
        plays.append(argv[-1])
        cancelled.set()
    controller._run_audio = play
    try:
        with pytest.raises(_Cancelled):
            controller._sentence_pipeline('First. Second.', tmp_path, produce, 'player', cancelled)
        assert len(plays) == 1
    finally:
        controller.close()


def test_synthesis_failure_after_first_clip_is_reported_without_late_play(tmp_path):
    controller = VoiceController({})
    def produce(chunks, stop):
        wav(tmp_path / '0000.wav')
        raise VoiceError('Synthetic failure')
    plays = []
    controller._run_audio = lambda argv, cancel, **kwargs: plays.append(argv[-1])
    controller.sentence_pause = 0
    try:
        with pytest.raises(VoiceError, match='Synthetic failure'):
            controller._sentence_pipeline('First. Second.', tmp_path, produce, 'player', threading.Event())
        assert len(plays) == 1
    finally:
        controller.close()


def test_batch_helper_loads_model_once_and_publishes_sentences_atomically(tmp_path, capsys):
    from scripts.british_voice import REQUIRED_FILES
    models = tmp_path / 'models'
    for name in REQUIRED_FILES:
        path = models / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'fixture')
    manifest = tmp_path / 'sentences.json'
    phrases = ['First sentence.', 'Second sentence.', 'Third sentence.', 'Fourth sentence.']
    manifest.write_text(json.dumps(phrases))
    output = tmp_path / 'audio'; output.mkdir()
    loads, generated = [], []
    class Engine:
        def generate(self, text, **kwargs):
            generated.append(text)
            return SimpleNamespace(samples=[.1] * 2400, sample_rate=24000)
    def load(path):
        loads.append(path)
        return Engine()
    waits = []
    def wait(delay):
        assert [p.name for p in sorted(output.glob('*.wav'))] == ['0000.wav', '0001.wav', '0002.wav']
        waits.append(delay)
        (output / 'consumed-0').touch()
    synthesize_segments(models, manifest, output, engine_factory=load, wait=wait)
    assert len(loads) == 1 and generated == phrases and waits == [.05]
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in output.glob('*.wav'))
    assert not any(phrase in capsys.readouterr().out for phrase in phrases)


def test_edge_padding_is_trimmed_without_cutting_speech(tmp_path):
    path = tmp_path / 'padded.wav'
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(24000)
        audio.writeframes(array('h', [0] * 2400 + [1000] * 4800 + [0] * 4800).tobytes())
    VoiceController._trim_silence(path)
    with wave.open(str(path)) as audio:
        samples = array('h', audio.readframes(audio.getnframes()))
    assert len(samples) == 6000 and sum(value == 1000 for value in samples) == 4800


def test_cancel_stops_synthesis_and_player_together(tmp_path):
    controller = VoiceController({})
    playing, processes, errors = threading.Event(), [], []
    cancelled = threading.Event()
    controller._active_cancel = cancelled
    class Process:
        returncode = None
        def __init__(self, argv, **kwargs):
            processes.append(self)
            if argv[0] == 'synthesize':
                wav(tmp_path / '0000.wav')
            else:
                playing.set()
        def poll(self): return self.returncode
        def send_signal(self, signal): self.returncode = -signal
        def wait(self, timeout): return self.returncode
        def kill(self): self.returncode = -9
    controller._popen = Process
    def produce(chunks, stop):
        controller._run_audio(['synthesize'], stop, limit=10)
    def run():
        try:
            controller._sentence_pipeline('First. Second.', tmp_path, produce, 'player', cancelled)
        except _Cancelled:
            pass
        except Exception as error:
            errors.append(error)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert playing.wait(2) and len(processes) == 2
        controller.cancel()
        worker.join(timeout=2)
        assert not worker.is_alive() and not errors
        assert all(p.poll() is not None for p in processes)
        assert not controller._processes and not controller._process_tasks
    finally:
        cancelled.set()
        controller.close()


def test_cancelled_http_producer_files_are_erased_after_it_exits():
    controller, release = VoiceController({}), threading.Event()
    try:
        with controller._speech_directory() as (directory, finished):
            finished.clear()
            path = directory
            (path / 'private.txt').write_text('Synthetic private data')
            def finish():
                release.wait(2)
                finished.set()
            producer = threading.Thread(target=finish)
            producer.start()
        assert path.exists()
        release.set()
        producer.join(timeout=2)
        deadline = time.monotonic() + 1
        while path.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert not path.exists()
    finally:
        release.set()
        controller.close()
