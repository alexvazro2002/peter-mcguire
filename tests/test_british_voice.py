"""British stock voice helper: local-only config, bounded audio, no silent fallback."""
from pathlib import Path
from types import SimpleNamespace
import json
import wave

import pytest

from scripts import british_voice


@pytest.fixture
def model(tmp_path):
    root = tmp_path / "model"
    for name in british_voice.REQUIRED_FILES:
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b"model-fixture")
    return root


def test_engine_uses_real_kokoro_config_with_british_phonemes_and_cpu(model):
    seen = {}
    def kokoro(**kw): seen["kokoro"] = kw; return kw
    def model_config(**kw): seen["model"] = kw; return kw
    def config(**kw): seen["config"] = kw; return SimpleNamespace(validate=lambda: True)
    sentinel = object()
    sherpa = SimpleNamespace(OfflineTtsKokoroModelConfig=kokoro, OfflineTtsModelConfig=model_config,
                             OfflineTtsConfig=config, OfflineTts=lambda config: sentinel)
    assert british_voice.create_engine(model, sherpa=sherpa) is sentinel
    assert seen["kokoro"]["lang"] == "en"  # bundled English (Great Britain) voice-file identifier
    assert seen["kokoro"]["model"].endswith("model.int8.onnx")
    assert seen["kokoro"]["voices"].endswith("voices.bin")
    assert seen["model"]["provider"] == "cpu" and seen["model"]["num_threads"] == 4
    assert seen["model"]["debug"] is False


def test_synthesis_selects_george26_and_writes_private_pcm_wav(model, tmp_path):
    calls = []
    class Engine:
        def generate(self, text, **kwargs):
            calls.append((text, kwargs)); return SimpleNamespace(samples=[0.0, 0.5, -0.5] * 8000, sample_rate=24000)
    output = tmp_path / "spoken.wav"
    result = british_voice.synthesize(model, "Good evening. I am ready.", output, engine_factory=lambda _: Engine())
    assert calls == [("Good evening. I am ready.", {"sid": 26, "speed": 1.0})]
    assert result["language"] == "en-gb" and result["phonemizer_voice"] == "en" and result["duration_seconds"] == 1
    assert "Good evening" not in json.dumps(result)
    with wave.open(str(output)) as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getnframes()) == (1, 2, 24000, 24000)
    assert output.stat().st_mode & 0o777 == 0o600


def test_all_british_voice_ids_are_explicit_and_unrecognised_never_falls_back(model, tmp_path):
    assert british_voice.VOICE_IDS == {"bm_daniel": 24, "bm_fable": 25, "bm_george": 26, "bm_lewis": 27}
    with pytest.raises(british_voice.BritishVoiceError, match="stock voice"):
        british_voice.synthesize(model, "Hello", tmp_path / "speech.wav", voice="jarvis")


@pytest.mark.parametrize("speed", [0, .69, 1.41, float("nan"), float("inf")])
def test_invalid_speed_is_rejected_before_model_load(model, speed):
    with pytest.raises(british_voice.BritishVoiceError, match="speed"):
        british_voice.validate_request(model, "bm_george", speed)


def test_missing_model_does_not_download_or_open_network(tmp_path):
    with pytest.raises(british_voice.BritishVoiceError, match="installer"):
        british_voice.validate_request(tmp_path, "bm_george", .95)


@pytest.mark.parametrize("text", ["", "!!!", "a" * 8001])
def test_input_limits(tmp_path, text):
    path = tmp_path / "text.txt"; path.write_text(text)
    with pytest.raises(british_voice.BritishVoiceError, match="characters"):
        british_voice.load_text(path)


def test_invalid_audio_and_existing_outputs_are_preserved(tmp_path):
    output = tmp_path / "existing.wav"; output.write_bytes(b"do not overwrite")
    with pytest.raises(british_voice.BritishVoiceError, match="new temporary"):
        british_voice.write_wav(output, [0.0] * 100, 24000)
    assert output.read_bytes() == b"do not overwrite"
    with pytest.raises(british_voice.BritishVoiceError, match="empty"):
        british_voice.write_wav(tmp_path / "empty.wav", [], 24000)
    with pytest.raises(british_voice.BritishVoiceError, match="invalid"):
        british_voice.write_wav(tmp_path / "invalid.wav", [float("nan")], 24000)


def test_upstream_errors_cannot_echo_utterance_to_ui(model, tmp_path):
    class Engine:
        def generate(self, text, **kwargs): raise ValueError(text)
    secret_text = "Private synthetic test phrase"
    with pytest.raises(british_voice.BritishVoiceError) as error:
        british_voice.synthesize(model, secret_text, tmp_path / "speech.wav", engine_factory=lambda _: Engine())
    assert secret_text not in str(error.value)
