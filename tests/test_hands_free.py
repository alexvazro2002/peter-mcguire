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


def test_interrupt_onset_requires_sustained_audible_speech_and_keeps_its_opening():
    loud = b'\xd0\x07' * 480
    detector = UtteranceDetector(vad=Vad(), onset_window=10, onset_required=8,
                                 minimum_rms=150, preroll_frames=20)
    for _ in range(40):
        assert detector.feed(SPEECH) == (False, None)  # Speech-like quiet noise.
    for _ in range(3):
        assert detector.feed(loud) == (False, None)
    for _ in range(10):
        assert detector.feed(SILENCE) == (False, None)
    opening = b'\xb0\x04' * 480
    assert detector.feed(opening) == (False, None)
    for _ in range(6):
        assert detector.feed(loud) == (False, None)
    assert detector.feed(loud) == (True, None)
    for _ in range(10): detector.feed(loud)
    pcm = None
    for _ in range(30):
        _, clip = detector.feed(SILENCE)
        pcm = clip or pcm
    assert pcm is not None and opening + loud * 17 in pcm
