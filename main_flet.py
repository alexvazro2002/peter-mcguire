"""Desktop entry point. --web is a local UI-preview option only."""
import argparse
import flet as ft
from app.panel import main
from app.config import BASE_DIR

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", action="store_true")
    parser.add_argument("--port", type=int, default=8550)
    args = parser.parse_args()
    ft.app(target=main, view=ft.AppView.WEB_BROWSER if args.web else ft.AppView.FLET_APP,
           port=args.port if args.web else 0, host="127.0.0.1", assets_dir=str(BASE_DIR / "assets"))
