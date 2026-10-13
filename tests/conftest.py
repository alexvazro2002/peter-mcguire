import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Tests must never modify the bundled personal notes/config.
os.environ["NEXUS_DATA_DIR"] = tempfile.mkdtemp(prefix="nexus-tests-")
