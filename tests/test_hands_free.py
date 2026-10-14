from app.hands_free import UtteranceDetector

SILENCE = b'\0\0' * 480
SPEECH = b'\x01\0' * 480


class Vad:
    def is_speech(self, frame, rate):
        assert rate == 16000
        return frame != SILENCE


def test_local_silence_never_forms_an_utterance_and_buffers_are_bounded():
    detector = UtteranceDetector(vad=Vad())
    for _ in range(10000):
        assert detector.feed(SILENCE) == (False, None)
    assert len(detector.preroll) == 10 and not detector.frames


def test_pause_endpoints_preserve_preroll_and_short_internal_pauses():
    detector = UtteranceDetector(vad=Vad())
    for _ in range(10): detector.feed(SILENCE)
    assert detector.feed(SPEECH) == (False, None)
    detector.feed(SPEECH); detector.feed(SPEECH)
    assert detector.feed(SPEECH) == (True, None)
    for _ in range(20): detector.feed(SPEECH)
    for _ in range(15): assert detector.feed(SILENCE)[1] is None
    for _ in range(10): detector.feed(SPEECH)
    for _ in range(29): assert detector.feed(SILENCE)[1] is None
    _, pcm = detector.feed(SILENCE)
    assert pcm.startswith(SILENCE) and pcm.endswith(SILENCE * 30) and SPEECH * 20 in pcm
    assert not detector.active and not detector.frames


def test_short_noise_is_discarded_and_continuous_speech_is_bounded():
    detector = UtteranceDetector(vad=Vad(), max_seconds=5)
    for _ in range(4): detector.feed(SPEECH)
    for _ in range(30): assert detector.feed(SILENCE)[1] is None
    result = None
    for _ in range(167):
        _, clip = detector.feed(SPEECH)
        if clip: result = clip
    assert result is not None and len(result) <= 167 * detector.frame_bytes


def test_real_webrtc_vad_rejects_digital_silence():
    detector = UtteranceDetector()
    for _ in range(100): assert detector.feed(SILENCE) == (False, None)
