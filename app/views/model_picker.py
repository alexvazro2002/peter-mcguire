"""One model chooser shared by Assistant and AI Library launches."""
from pathlib import Path

import flet as ft

from app.theme import label
from library.models import model_choices


def choose_model(app, use_case, on_selected):
    choices = model_choices(app.cfg["library"])
    by_key = {choice.key: choice for choice in choices}
    current = app.cfg["library"].get("model_id", "qwen")
    default = current if current in by_key and by_key[current].chat else "qwen"
    options = [ft.dropdown.Option(choice.key, choice.title + (" (OCR only)" if not choice.chat else ""), disabled=not choice.chat)
               for choice in choices]
    selector = ft.Dropdown(label="AI model", value=default, options=options, width=550, color=app.c["text"], border_color=app.c["border"])
    detail = ft.Text(size=12, color=app.c["muted"])
    path = ft.Text(size=11, color=app.c["dim"], selectable=True)
    error = ft.Text(size=12, color=app.c["danger"])
    dialog = ft.AlertDialog(title=ft.Text("Choose a model for " + use_case, color=app.c["text"]), bgcolor=app.c["surface"])
    def refresh(_=None):
        choice = by_key[selector.value]
        detail.value = choice.recommendation
        path.value = choice.path or app.cfg["library"]["server_url"]
        if choice.path and not Path(choice.path).is_file():
            path.value += "\nNot found at this location. Configure the model folder in Settings."
        error.value = ""
        if _ is not None:
            dialog.update()
    selector.on_change = refresh
    refresh()
    def launch(_):
        try:
            app.select_model(by_key[selector.value])
            app.page.close(dialog)
            on_selected()
        except (OSError, ValueError, RuntimeError) as exc:
            error.value = str(exc)
            dialog.update()
    dialog.content = ft.Container(ft.Column([
        selector, detail, path, ft.Divider(color=app.c["border"]),
        label("Qwen: Assistant actions and Library answers. Gemma: alternate general chat and code. PaddleOCR: scanned-document extraction only.", app.c, 11),
        error], spacing=14, tight=True), width=550)
    dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: app.page.close(dialog)),
                      ft.TextButton("Launch", on_click=launch)]
    app.page.open(dialog)
    return dialog
