#!/usr/bin/env python3
"""One-shot British stock-voice synthesis using Peter's installed Sherpa runtime.

The caller chooses the isolated Python interpreter and handles cancellation by
stopping this child. This helper never downloads models or plays/records audio.
It emits metrics as JSON, with no utterance text in stdout or error reports.

Model: csukuangfj/kokoro-int8-multi-lang-v1_0, Apache-2.0.
Speaker mapping and API: https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_0.html
"""
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
import sys
import tempfile
import time
import wave
from pathlib import Path

VOICE_IDS = {"bm_daniel": 24, "bm_fable": 25, "bm_george": 26, "bm_lewis": 27}
# The bundled eSpeak voice file is named "en" and describes English (Great
# Britain). Sherpa calls SetVoiceByName; its BCP-47 alias "en-gb" fails here.
ESPEAK_VOICE = "en"
MODEL_REPO = "csukuangfj/kokoro-int8-multi-lang-v1_0"
MODEL_REVISION = "2a360693d79b88b49b88e29aec2b53577f41f206"
REQUIRED_FILES = ("model.int8.onnx", "voices.bin", "tokens.txt", "espeak-ng-data/phontab",
                  "espeak-ng-data/phonindex", "espeak-ng-data/phondata", "espeak-ng-data/intonations",
                  "espeak-ng-data/en_dict", "espeak-ng-data/lang/gmw/en")


class BritishVoiceError(RuntimeError):
    pass


def load_text(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > 32000:
        raise BritishVoiceError("The speech input file is missing or larger than 32 KB.")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise BritishVoiceError("The speech input file must contain UTF-8 text.") from error
    text = "".join(character for character in text if character in "\n\t" or ord(character) >= 32).strip()
    if not text or len(text) > 8000 or not any(character.isalnum() for character in text):
        raise BritishVoiceError("Speech must contain 1–8,000 characters with at least one word.")
    return text


def validate_request(model_dir, voice, speed):
    if voice not in VOICE_IDS:
        raise BritishVoiceError("Choose a supported British male stock voice.")
    if not math.isfinite(speed) or not 0.7 <= speed <= 1.4:
        raise BritishVoiceError("Speech speed must be between 0.7 and 1.4.")
    model_dir = Path(model_dir).expanduser().resolve()
    if any(not (model_dir / name).is_file() for name in REQUIRED_FILES):
        raise BritishVoiceError("British voice files are incomplete. Run the explicit British voice installer first.")
    return model_dir


def create_engine(model_dir, *, sherpa=None):
    if sherpa is None:
        try:
            import sherpa_onnx as sherpa
        except ImportError as error:
            raise BritishVoiceError("Run this helper with the installed VoiceStudio Python runtime containing sherpa-onnx.") from error
    config = sherpa.OfflineTtsConfig(model=sherpa.OfflineTtsModelConfig(
        kokoro=sherpa.OfflineTtsKokoroModelConfig(
            model=str(model_dir / "model.int8.onnx"), voices=str(model_dir / "voices.bin"),
            tokens=str(model_dir / "tokens.txt"), data_dir=str(model_dir / "espeak-ng-data"),
            lang=ESPEAK_VOICE), provider="cpu", num_threads=4, debug=False), max_num_sentences=1)
    if not config.validate():
        raise BritishVoiceError("Sherpa rejected the installed British voice model configuration.")
    return sherpa.OfflineTts(config)


def write_wav(output, samples, sample_rate):
    if sample_rate != 24000 or not 0 < len(samples) <= sample_rate * 180:
        raise BritishVoiceError("British synthesis returned empty, oversized or unsupported audio.")
    if any(not math.isfinite(value) for value in samples):
        raise BritishVoiceError("British synthesis returned invalid audio samples.")
    output = Path(output)
    if output.is_symlink() or output.exists():
        raise BritishVoiceError("Choose a new temporary output WAV path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".peter-british-", suffix=".wav", dir=output.parent)
    try:
        os.close(descriptor)
        pcm = array("h", (int(max(-1.0, min(1.0, float(value))) * 32767) for value in samples))
        if sys.byteorder != "little":
            pcm.byteswap()
        with wave.open(temporary, "wb") as audio:
            audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(sample_rate)
            audio.writeframes(pcm.tobytes())
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def synthesize(model_dir, text, output, *, speed=1.0, voice="bm_george", engine_factory=create_engine):
    model_dir = validate_request(model_dir, voice, speed)
    started = time.monotonic()
    engine = engine_factory(model_dir)
    loaded = time.monotonic()
    try:
        # Explicit British G2P and a genuine British stock speaker. No speaker
        # prompt, actor recording, remote service or fallback voice is involved.
        result = engine.generate(text, sid=VOICE_IDS[voice], speed=speed)
    except Exception as error:
        # Upstream exceptions can include the source utterance; keep the
        # user-facing error generic, without echoing text or file contents.
        raise BritishVoiceError("British voice synthesis failed. Check the local runtime and installed model files.") from error
    generated = time.monotonic()
    write_wav(output, result.samples, result.sample_rate)
    duration = len(result.samples) / result.sample_rate
    return dict(model=MODEL_REPO, revision=MODEL_REVISION, voice=voice, speaker_id=VOICE_IDS[voice],
                language="en-gb", phonemizer_voice=ESPEAK_VOICE, sample_rate=result.sample_rate, frames=len(result.samples),
                duration_seconds=round(duration, 3), load_seconds=round(loaded - started, 3),
                generation_seconds=round(generated - loaded, 3),
                real_time_factor=round((generated - loaded) / duration, 3))


def synthesize_segments(model_dir, manifest, directory, *, speed=1.0, voice="bm_george",
                        engine_factory=create_engine, wait=time.sleep):
    """Load once, publish each atomic WAV, and stay at most three clips ahead."""
    model_dir = validate_request(model_dir, voice, speed)
    manifest, directory = Path(manifest), Path(directory)
    if manifest.stat().st_size > 64000 or not directory.is_dir() or directory.is_symlink():
        raise BritishVoiceError("Invalid private sentence input or output directory.")
    texts = json.loads(manifest.read_text(encoding='utf-8'))
    if (not isinstance(texts, list) or not texts or len(texts) > 1000 or
            any(not isinstance(text, str) or not text.strip() or len(text) > 4096 for text in texts) or
            sum(map(len, texts)) > 32000):
        raise BritishVoiceError("Invalid or oversized sentence list.")
    loaded_at = time.monotonic()
    engine = engine_factory(model_dir)
    load_seconds = time.monotonic() - loaded_at
    for index, text in enumerate(texts):
        deadline = time.monotonic() + 300
        while index >= 3 and not (directory / f'consumed-{index - 3}').exists():
            if time.monotonic() > deadline:
                raise BritishVoiceError("Speech playback stopped acknowledging sentences.")
            wait(.05)
        started = time.monotonic()
        try:
            result = engine.generate(text, sid=VOICE_IDS[voice], speed=speed)
        except Exception as error:
            raise BritishVoiceError("British sentence synthesis failed.") from error
        write_wav(directory / f'{index:04d}.wav', result.samples, result.sample_rate)
        print(json.dumps(dict(segment=index, duration_seconds=round(len(result.samples) / result.sample_rate, 3),
                              generation_seconds=round(time.monotonic() - started, 3),
                              load_seconds=round(load_seconds, 3) if index == 0 else 0)), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--text-file", type=Path)
    inputs.add_argument("--segments-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--voice", choices=tuple(VOICE_IDS), default="bm_george")
    args = parser.parse_args(argv)
    try:
        if args.segments_file:
            if args.output_dir is None or args.output is not None:
                raise BritishVoiceError("Sentence synthesis requires its private output directory.")
            synthesize_segments(args.model_dir, args.segments_file, args.output_dir, speed=args.speed, voice=args.voice)
            return 0
        if args.output is None:
            raise BritishVoiceError("Choose a temporary output WAV path.")
        metadata = synthesize(args.model_dir, load_text(args.text_file), args.output,
                              speed=args.speed, voice=args.voice)
        print(json.dumps(metadata), flush=True)
        return 0
    except (BritishVoiceError, OSError, ValueError) as error:
        print(str(error) if isinstance(error, BritishVoiceError) else "British voice could not complete the local request.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
