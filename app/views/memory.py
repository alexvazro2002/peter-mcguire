"""Native dashboard over the same validated MemoryStore used by Peter's tools."""
from datetime import datetime

import flet as ft

from app.memory import CATEGORIES
from app.theme import button, field, heading, label, panel, pill


class MemoryView:
    def __init__(self, app):
        self.app, self.c, self.query, self.category = app, app.c, "", ""
        self.temporary, self.pinned_only = None, False
        self.store = app.assistant.memory
        self.rows = ft.ListView(expand=True, spacing=10)
        self.count = label("", self.c, 11)
        self.content = ft.Column([
            heading("Personal Memory", "The facts and preferences Peter can remember.", self.c,
                    [button("Add memory", lambda _: self.edit(), self.c, ft.Icons.ADD, primary=True)]),
            ft.Row([field(self.c, hint_text="Search memories…", prefix_icon=ft.Icons.SEARCH,
                          expand=True, max_length=1000, on_change=self.search),
                    ft.Dropdown(value="", width=230, color=self.c["text"], border_color=self.c["border"],
                                options=[ft.dropdown.Option("", "All categories"),
                                         *[ft.dropdown.Option(k, v) for k, v in CATEGORIES.items()]],
                                on_change=self.filter)], spacing=12),
            ft.Row([ft.Dropdown(label="Keep for", value="all", width=220,
                                color=self.c["text"], border_color=self.c["border"],
                                options=[ft.dropdown.Option("all", "Permanent and temporary"),
                                         ft.dropdown.Option("permanent", "Permanent"),
                                         ft.dropdown.Option("temporary", "Temporary")], on_change=self.permanence),
                    ft.Checkbox(label="Pinned only", value=False, on_change=self.pins)], spacing=14),
            ft.Row([self.count, ft.Container(expand=True),
                    button("Export JSON", lambda _: self.app.export(self.store.export_json(), "personal-memory.json"),
                           self.c, ft.Icons.DOWNLOAD_OUTLINED),
                    button("Clear temporary", self.clear_temporary, self.c, ft.Icons.AUTO_DELETE_OUTLINED)], spacing=10),
            self.rows,
            label("Stored locally · Shared with Peter · Edit or remove facts whenever you wish.", self.c, 11)
        ], expand=True, spacing=16)
        self.refresh()

    def search(self, event):
        self.query = event.control.value or ""
        self.refresh(True)

    def filter(self, event):
        self.category = event.control.value or ""
        self.refresh(True)

    def permanence(self, event):
        self.temporary = {"all": None, "permanent": False, "temporary": True}[event.control.value]
        self.refresh(True)

    def pins(self, event):
        self.pinned_only = bool(event.control.value)
        self.refresh(True)

    def refresh(self, update=False):
        c = self.c
        try:
            entries = self.store.list(self.query, self.category, self.temporary)
            if self.pinned_only:
                entries = [entry for entry in entries if entry["pinned"]]
            self.count.value = f"{len(entries)} memor{'y' if len(entries) == 1 else 'ies'}"
            rows = []
            for entry in entries:
                badges = [pill(CATEGORIES[entry["category"]], c["accent"], c)]
                if entry["pinned"]:
                    badges.append(pill("Pinned", c["cyan"], c))
                if entry["temporary"]:
                    badges.append(pill("Temporary", c["amber"], c))
                rows.append(panel(ft.Column([
                    ft.Row([*badges, ft.Container(expand=True),
                            ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit memory", icon_color=c["muted"],
                                          on_click=lambda _, m=entry: self.edit(m)),
                            ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete memory", icon_color=c["muted"],
                                          on_click=lambda _, m=entry: self.delete(m))], spacing=8, wrap=True),
                    ft.Text(entry["content"], color=c["text"], size=14, selectable=True),
                    label(f"{entry['source']} · Updated {datetime.fromisoformat(entry['updated_at']).astimezone():%d %b %Y}", c, 11)
                ], spacing=10), c, padding=18))
            self.rows.controls = rows or [panel(ft.Column([
                ft.Icon(ft.Icons.PSYCHOLOGY_OUTLINED, color=c["cyan"], size=32),
                ft.Text("A fresh memory.", size=21, color=c["text"]),
                label("Add a fact here, or tell Peter what you would like him to remember.", c)
            ], spacing=14), c, padding=28)]
        except (OSError, ValueError) as error:
            self.rows.controls = [ft.Text(str(error), color=c["danger"], selectable=True)]
        if update:
            self.content.update()

    def edit(self, entry=None):
        original = entry
        entry, c = entry or {}, self.c
        text = field(c, label="Memory", value=entry.get("content", ""), multiline=True,
                     min_lines=3, max_lines=6, max_length=4000, autofocus=True)
        category = ft.Dropdown(label="Category", value=entry.get("category", "personal"), color=c["text"],
                               border_color=c["border"], options=[ft.dropdown.Option(k, v) for k, v in CATEGORIES.items()])
        source = field(c, label="Source", value=entry.get("source", "Manual entry"), max_length=300)
        pinned = ft.Checkbox(label="Pinned — include in Peter's context", value=entry.get("pinned", False))
        temporary = ft.Checkbox(label="Temporary context", value=entry.get("temporary", False))
        error = ft.Text(color=c["danger"], size=12)
        dialog = ft.AlertDialog(title=ft.Text("Edit memory" if entry else "Add memory", color=c["text"]),
                                bgcolor=c["surface"], content=ft.Container(ft.Column([
                                    text, category, source, pinned, temporary, error], spacing=12,
                                    scroll=ft.ScrollMode.AUTO), width=560))
        def save(_):
            try:
                if original and self.store.get(original["id"]) != original:
                    raise ValueError("This memory changed while the editor was open. Reopen it before saving.")
                self.store.save(dict(content=text.value or "", category=category.value,
                                     source=source.value or "", pinned=pinned.value, temporary=temporary.value), entry.get("id"))
                self.app.page.close(dialog)
                self.refresh(True)
            except (OSError, ValueError) as exc:
                error.value = str(exc)
                dialog.update()
        dialog.actions = [ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dialog)),
                          ft.TextButton("Save", on_click=save)]
        self.app.page.open(dialog)

    def delete(self, entry):
        def remove():
            if self.store.get(entry["id"]) != entry:
                raise ValueError("This memory changed. Refresh before deleting it.")
            self.store.delete(entry["id"], confirmed=True)
            self.refresh(True)
        self.app.confirm("Delete this memory?", "This removes the saved fact from Peter's current memory.", remove)

    def clear_temporary(self, _):
        def clear():
            count = self.store.clear_temporary(confirmed=True)
            self.refresh(True)
            self.app.notify(f"Cleared {count} temporary memories.")
        self.app.confirm("Clear temporary memories?", "Permanent memories stay available. Pinned temporary memories are also cleared.", clear)
