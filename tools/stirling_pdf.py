"""One-click Stirling-PDF launch entry point."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.docker_services import main

if __name__ == "__main__":
    raise SystemExit(main("stirling_pdf"))
