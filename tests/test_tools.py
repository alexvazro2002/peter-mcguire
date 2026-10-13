import importlib.util
import subprocess
import sys
from pathlib import Path
from PIL import Image

from app.config import BASE_DIR
from app.tools import TOOLS, tool_command


def test_converter_command_quotes_are_not_shell_code(tmp_path):
    source = tmp_path / "picture with spaces.png"
    Image.new("RGB", (10, 10), "red").save(source)
    tool = next(t for t in TOOLS if t.key == "converter")
    command = tool_command(tool, {"file": str(source), "output": str(tmp_path / "output"), "to": "jpg"})
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    target = tmp_path / "output" / "picture with spaces.jpg"
    before = target.read_bytes()
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 1 and target.read_bytes() == before


def test_flac_worker_only_copies_new_files(tmp_path):
    source, destination = tmp_path / "source", tmp_path / "existing playlist"
    source.mkdir()
    destination.mkdir()
    (source / "01 Artist - Track.flac").write_bytes(b"test invalid flac uses filename fallback")
    untouched = destination / "keep-this.flac"
    untouched.write_bytes(b"do not rename or modify")
    script = BASE_DIR / "tools" / "collect_and_rename_flacs.py"
    result = subprocess.run([sys.executable, str(script), "--source", str(source), "--destination", str(destination)],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert untouched.read_bytes() == b"do not rename or modify"
    assert (tmp_path / "existing playlist (2)" / "Track - Artist.flac").exists()
    assert (source / "01 Artist - Track.flac").exists()
