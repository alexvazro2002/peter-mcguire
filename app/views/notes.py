"""Markdown-first notes: safe autosave, wiki links, backlinks, and an interactive graph."""
import asyncio
import time
from urllib.parse import quote, unquote
import flet as ft

from app.storage import links, transform_links
from app.config import atomic_write
from app.theme import button, field, heading, label, panel, pill
from app.views.graph import NoteGraph


class EditorConflict(ValueError):
    pass


class NotesView:
    def __init__(self, app):
        self.app, self.c = app, app.c
        self.query, self.revision = "", 0
        self.status = ft.Text("Saved locally", size=11, color=self.c["dim"])
        self.note_list = ft.Container(expand=True)
        self.editor = None
        self.graph = None
        self.saved_content = app.vault.read(app.note_name) if app.note_name else ""
        self.content = ft.Column(expand=True, spacing=16)
        self.refresh_list()
        self.render_note()

    def save(self):
        if self.editor is not None and self.app.note_name:
            if self.app.note_name not in self.app.vault.names():
                raise EditorConflict("This note was deleted outside the editor. Your unsaved text has been preserved.")
            current = self.app.vault.read(self.app.note_name)
            draft = self.editor.value or ""
            if current != self.saved_content:
                if draft != self.saved_content:
                    raise EditorConflict("This note changed outside the editor. Your unsaved text is still here; reload or export it before reopening the updated note.")
                draft = current
                self.editor.value = current
            else:
                self.app.vault.write(self.app.note_name, draft)
            self.app.note_draft = self.saved_content = draft
            self.status.value = "Saved locally"

    def recover_draft(self):
        if self.editor is not None:
            backup = self.app.vault.root / ".history" / f"editor-conflict-{time.time_ns()}-{self.app.note_name}.md"
            atomic_write(backup, self.editor.value or "")
        self.revision += 1
        self.editor = None
        self.app.ensure_note()

    def change(self, event):
        self.app.note_draft = event.control.value or ""
        self.revision += 1
        revision = self.revision
        self.status.value = "Saving…"
        self.status.update()

        async def debounce():
            await asyncio.sleep(0.7)
            if revision == self.revision and self.app.notes_view is self:
                try:
                    self.save()
                    self.status.update()
                except (OSError, ValueError) as error:
                    self.status.value = "Save failed"
                    self.app.notify(str(error), True)
        self.app.page.run_task(debounce)

    def visible_names(self):
        names = self.app.vault.names()
        query = self.query.casefold().strip()
        if query:
            names = [n for n in names if query in n.casefold() or query in self.app.vault.read(n).casefold()]
        config = self.app.cfg
        order = {name: i for i, name in enumerate(config["notes_order"])}
        sort = config.get("notes_sort", "custom")
        if sort in {"recent", "last_used"}:
            names.sort(key=lambda n: config["notes_last_used"].get(n, 0), reverse=True)
        elif sort == "custom":
            names.sort(key=lambda n: order.get(n, 9999))
        elif sort == "alpha_desc":
            names.reverse()
        if sort != "custom":
            names.sort(key=lambda n: n not in config["notes_pinned"])
        return names

    def refresh_list(self, event=None):
        if event:
            self.query = event.control.value or ""
        c = self.c
        rows = [
            ft.Container(ft.Row([ft.Icon(ft.Icons.PUSH_PIN_OUTLINED if n in self.app.cfg["notes_pinned"] else ft.Icons.DESCRIPTION_OUTLINED,
                                        size=15, color=c["accent"] if n == self.app.note_name else c["dim"]),
                                  ft.Text(n, size=12, color=c["text"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS, expand=True)], spacing=9),
                         padding=ft.padding.symmetric(horizontal=12, vertical=12), border_radius=9,
                         bgcolor=c["raised"] if n == self.app.note_name else None,
                         on_click=lambda e, name=n: self.app.open_note(name), ink=True, key=n)
            for n in self.visible_names()]
        self.note_list.content = (ft.ReorderableListView(rows, expand=True, show_default_drag_handles=True,
                                                       on_reorder=self.drag_reorder)
                                  if self.app.cfg.get("notes_sort") == "custom" else ft.ListView(rows, expand=True, spacing=3))
        if event:
            self.note_list.update()

    def drag_reorder(self, event):
        if self.app.cfg.get("notes_sort") != "custom":
            return
        visible = self.visible_names()
        old, new = event.old_index, event.new_index
        if not isinstance(old, int) or not isinstance(new, int) or not 0 <= old < len(visible) or not 0 <= new <= len(visible):
            return
        # Flet already normalizes the destination index after removing the source.
        reordered = list(visible)
        reordered.insert(new, reordered.pop(old))
        names = self.app.vault.names()
        order = list(dict.fromkeys(n for n in self.app.cfg["notes_order"] if n in names))
        order += [n for n in names if n not in order]
        # Keep filtered-out notes in their original slots while reordering visible notes.
        replacements = iter(reordered)
        self.app.cfg["notes_order"] = [next(replacements) if n in visible else n for n in order]
        self.app.persist()
        self.refresh_list()
        self.note_list.update()

    def mode(self, mode):
        self.save()
        self.app.note_mode = mode
        self.render_note()
        self.content.update()

    def new_note(self, _=None):
        self.app.prompt("Create a note", "Note title", "", lambda name: self.app.open_note(self.app.vault.create(name)))

    def rename(self, _=None):
        old = self.app.note_name
        def submit(new):
            new = new.strip()
            self.save()
            self.app.vault.rename(old, new)
            for key in ("notes_order", "notes_pinned"):
                self.app.cfg[key] = [new if n == old else n for n in self.app.cfg[key]]
            for key in ("notes_last_used", "notes_graph_pos"):
                if old in self.app.cfg[key]:
                    self.app.cfg[key][new] = self.app.cfg[key].pop(old)
            self.app.persist()
            self.editor = None
            self.app.open_note(new)
        self.app.prompt("Rename note", "New title", old, submit)

    def trash(self, _=None):
        def remove():
            self.save()
            old = self.app.note_name
            self.app.vault.trash(old)
            self.app.cfg["notes_pinned"] = [n for n in self.app.cfg["notes_pinned"] if n != old]
            self.app.persist()
            self.editor = None
            self.app.ensure_note()
            self.app.navigate("notes")
            self.app.notify("Note moved to your vault's .trash folder; it can be restored.")
        self.app.confirm("Move note to trash?", "The Markdown file stays recoverable in your vault's .trash folder.", remove)

    def pin(self, _=None):
        pins, name = self.app.cfg["notes_pinned"], self.app.note_name
        self.app.cfg["notes_pinned"] = [n for n in pins if n != name] if name in pins else pins + [name]
        self.app.persist()
        self.refresh_list()
        self.note_list.update()

    def duplicate(self, _=None):
        self.save()
        self.app.open_note(self.app.vault.duplicate(self.app.note_name))

    def reorder(self, amount):
        if self.app.cfg.get("notes_sort") != "custom":
            return
        names = self.app.vault.names()
        order = [n for n in self.app.cfg["notes_order"] if n in names]
        order += [n for n in names if n not in order]
        i = order.index(self.app.note_name)
        order.insert(max(0, min(len(order) - 1, i + amount)), order.pop(i))
        self.app.cfg.update(notes_order=order, notes_sort="custom")
        self.app.persist()
        self.refresh_list()
        self.note_list.update()

    def insert_link(self, _=None):
        if not self.app.note_name:
            self.new_note()
            return
        if self.app.note_mode != "write":
            self.mode("write")
        def append(target):
            target = target.strip()
            if target not in self.app.vault.names():
                self.app.vault.create(target)
            self.editor.value = (self.editor.value or "") + f" [[{target}]]"
            self.save()
            self.editor.update()
            self.refresh_list()
            self.note_list.update()
        self.app.prompt("Insert a wiki link", "Existing or new note title (appended to this note)", "", append)

    def render_note(self):
        app, c, name, mode = self.app, self.c, self.app.note_name, self.app.note_mode
        self.editor = None
        self.graph = None
        self.saved_content = app.vault.read(name) if name else ""
        if not name:
            self.content.controls = [heading("Notes", "A fresh, local knowledge space.", c,
                                             [button("New note", self.new_note, c, ft.Icons.ADD, primary=True)]),
                panel(ft.Column([
                    ft.Icon(ft.Icons.EDIT_NOTE_OUTLINED, size=46, color=c["cyan"]),
                    ft.Text("Room for your next idea.", size=25, color=c["text"]),
                    ft.Text("Create a note, or ask Peter to save something for you.", size=13, color=c["muted"],
                            text_align=ft.TextAlign.CENTER),
                    button("Create your first note", self.new_note, c, ft.Icons.ADD, primary=True),
                    label("Markdown files · Wiki links · Read, Write and Graph", c, 11)
                ], spacing=20, alignment=ft.MainAxisAlignment.CENTER,
                   horizontal_alignment=ft.CrossAxisAlignment.CENTER, expand=True), c, expand=True)]
            return
        toolbar = ft.Row([
            *[button(title, lambda e, m=key: self.mode(m), c, icon=icon, primary=mode == key)
              for key, title, icon in [("read", "Read", ft.Icons.MENU_BOOK_ROUNDED),
                                      ("write", "Write", ft.Icons.EDIT_OUTLINED),
                                      ("graph", "Graph", ft.Icons.HUB_OUTLINED)]],
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.ADD_LINK_ROUNDED, tooltip="Insert wiki link (Ctrl+L)", icon_color=c["muted"], on_click=self.insert_link),
            ft.IconButton(ft.Icons.SAVE_OUTLINED, tooltip="Save (Ctrl+S)", icon_color=c["muted"], on_click=lambda e: self.save()),
            ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ, icon_color=c["muted"], items=[
                ft.PopupMenuItem(text="Pin / unpin", on_click=self.pin), ft.PopupMenuItem(text="Rename", on_click=self.rename),
                ft.PopupMenuItem(text="Duplicate", on_click=self.duplicate),
                ft.PopupMenuItem(text="Move up", on_click=lambda e: self.reorder(-1), disabled=app.cfg.get("notes_sort") != "custom"),
                ft.PopupMenuItem(text="Move down", on_click=lambda e: self.reorder(1), disabled=app.cfg.get("notes_sort") != "custom"),
                ft.PopupMenuItem(text="Export Markdown", on_click=lambda e: app.export_note(name)),
                ft.PopupMenuItem(text="Move to trash", on_click=self.trash)]),
        ], spacing=5)
        if mode == "write":
            self.editor = field(c, value=app.note_draft, multiline=True, min_lines=18, max_lines=1000,
                                expand=True, on_change=self.change, border=ft.InputBorder.NONE,
                                text_style=ft.TextStyle(font_family="DejaVu Sans Mono", height=1.6), text_size=14, autofocus=True)
            body = self.editor
        elif mode == "graph":
            self.graph = graph = NoteGraph(app)
            body = ft.Column([ft.Row([pill(f"{len(graph.names)} nodes", c["cyan"], c),
                                      pill(f"{len(graph.edges)} links", c["accent"], c), ft.Container(expand=True),
                                      ft.IconButton(ft.Icons.CENTER_FOCUS_STRONG, on_click=graph.reset, tooltip="Reset view", icon_color=c["muted"])]),
                              graph, label("Drag nodes to arrange · drag background to pan · scroll to zoom · click links for passages", c, 11)],
                             expand=True, spacing=8)
        else:
            preview = transform_links(app.note_draft, lambda m: f"[{m.group(2) or m.group(1)}](nexus-note:{quote(m.group(1).strip(), safe='')})")
            def tap(event):
                if str(event.data).startswith("nexus-note:"):
                    app.open_note(unquote(event.data.split(":", 1)[1]))
                elif str(event.data).startswith(("https://", "http://")):
                    app.confirm("Open external link?", str(event.data), lambda: app.page.launch_url(event.data))
            body = ft.GestureDetector(on_double_tap=lambda e: self.mode("write"), content=ft.ListView([
                ft.Markdown(preview or "*This note is empty. Switch to Write to begin.*", selectable=True,
                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB, auto_follow_links=False, on_tap_link=tap,
                            md_style_sheet=ft.MarkdownStyleSheet(p_text_style=ft.TextStyle(size=14, color=c["text"], height=1.7),
                                                                a_text_style=ft.TextStyle(color=c["accent"])))], expand=True), expand=True)
        editor_controls = [toolbar, ft.Divider(height=1, color=c["border"]), body] if mode == "graph" else [
            ft.Row([ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, size=18, color=c["accent"]),
                    ft.Text(name, size=21, weight=ft.FontWeight.W_600, color=c["text"], expand=True), self.status]),
            toolbar, ft.Divider(height=1, color=c["border"]), body,
            ft.Row([label(f"{len(app.note_draft.split())} words · Markdown", c, 11), ft.Container(expand=True),
                    label("Stored on your device", c, 11)])]
        editor_panel = panel(ft.Column(editor_controls, spacing=10 if mode == "graph" else 14, expand=True),
                             c, padding=16 if mode == "graph" else 20, expand=True)
        sidebar = panel(ft.Column([
            ft.Row([label("YOUR VAULT", c, 10), ft.Container(expand=True),
                    ft.IconButton(ft.Icons.ADD, icon_size=18, icon_color=c["accent"], tooltip="New note", on_click=self.new_note)]),
            field(c, hint_text="Search notes…", prefix_icon=ft.Icons.SEARCH, on_change=self.refresh_list, value=self.query, dense=True),
            ft.Dropdown(value=app.cfg.get("notes_sort", "custom"), text_size=11, color=c["muted"], border_color=c["border"],
                        options=[ft.dropdown.Option(k, title) for k, title in [("custom", "Custom order"), ("alpha", "A–Z"),
                                                                             ("alpha_desc", "Z–A"), ("recent", "Recent")]], on_change=self.sort),
            self.note_list, ft.Divider(color=c["border"]), label(f"{len(app.vault.names())} local notes", c, 11)],
            expand=True, spacing=12), c, padding=14, width=228)
        connections = []
        if mode != "graph" and (app.page.width or 1440) >= 1320:
            connections = [self.connections_panel()]
        self.content.controls = [heading("Notes", "Your ideas. A connected, local knowledge space.", c),
                                 ft.Row([sidebar, editor_panel, *connections],
                                        spacing=14, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)]

    def connections_panel(self):
        app, c = self.app, self.c
        outgoing, incoming = links(app.note_draft), app.vault.backlinks(app.note_name)
        connection_controls = [label("CONNECTIONS", c, 10), ft.Icon(ft.Icons.HUB_OUTLINED, size=32, color=c["accent"]),
                               ft.Text("Ideas, connected.", color=c["text"], size=16), label("Link notes with [[Title]] or [[Title|label]].", c),
                               ft.Divider(color=c["border"])]
        for title, targets, color in [("OUTGOING", outgoing, c["accent"]), ("BACKLINKS", incoming, c["cyan"])]:
            connection_controls += [label(f"{title}  /  {len(targets)}", c, 10)]
            connection_controls += [ft.TextButton(n, on_click=lambda e, t=n: app.open_note(t), style=ft.ButtonStyle(color=color)) for n in targets]
            if not targets:
                connection_controls += [label("No links yet", c, 11)]
            connection_controls += [ft.Divider(color=c["border"])]
        return panel(ft.Column(connection_controls, spacing=12, scroll=ft.ScrollMode.AUTO), c, width=205)

    def sort(self, event):
        self.app.cfg["notes_sort"] = event.control.value
        self.app.persist()
        self.refresh_list()
        self.note_list.update()
