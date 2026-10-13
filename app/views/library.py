"""Two entry points into one persistent, local AI session."""
import flet as ft
import re

from app.theme import button, field, heading, label, panel, pill
from library.search import tokens


class LibraryView:
    def __init__(self, app):
        self.app, self.c = app, app.c
        c = self.c
        self.custom = field(c, label="Processed files or library directory", value=app.cfg["library"].get("last_source", ""), expand=True)
        self.custom_panel = panel(ft.Column([
            label("CHOOSE YOUR SOURCE", c, 10),
            ft.Row([self.custom, button("Browse", lambda e: app.pick(self.custom, directory=True), c, ft.Icons.FOLDER_OPEN_OUTLINED)]),
            label("Accepts a folder of processed .txt files, a library root, _KNOWLEDGE_BASE, extracted, or an existing index.", c),
            button("Start AI Library", lambda e: app.start_library(self.custom.value), c, ft.Icons.ARROW_FORWARD, primary=True)], spacing=12), c, visible=False)
        self.status = ft.Text(app.library_status, size=12, color=c["cyan"])
        self.summary = ft.Text(self.summary_text(), size=12, color=c["muted"])
        self.modes = ft.Row(spacing=4)
        self.source_filter = ft.Dropdown(value=app.library_filter or "__all__", width=260, label="Source", color=c["text"],
                                         border_color=c["border"], text_size=12, enable_filter=True, enable_search=True,
                                         options=[ft.dropdown.Option("__all__", "All documents")],
                                         on_change=self.filter_source, disabled=not app.library_ready)
        self.processing_title = ft.Text(size=14, color=c["text"], weight=ft.FontWeight.W_600)
        self.processing_detail = ft.Text(size=12, color=c["muted"])
        self.processing_file = ft.Text(size=11, color=c["dim"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)
        self.processing_bar = ft.ProgressBar(color=c["accent"], bgcolor=c["raised"], height=6, border_radius=4)
        self.processing_panel = panel(ft.Column([
            ft.Row([ft.Icon(ft.Icons.LAYERS_OUTLINED, color=c["accent"], size=19), self.processing_title], spacing=10),
            self.processing_bar, self.processing_detail, self.processing_file], spacing=9), c, padding=16, visible=bool(app.library_progress))
        self.messages = ft.ListView(expand=True, spacing=16, auto_scroll=True)
        self.input = field(c, hint_text="Ask a question about your library…", expand=True, on_submit=self.ask,
                           disabled=not app.library_ready, max_length=8000)
        self.send = ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, bgcolor=c["accent"], icon_color=c["accent_text"],
                                  tooltip="Ask library", on_click=self.ask, disabled=not app.library_ready)
        self.activity = ft.Text("\n".join(app.library_logs[-6:]), font_family="Consolas", size=10, color=c["dim"], selectable=True)
        self.progress = ft.ProgressBar(color=c["accent"], bgcolor=c["raised"], visible=app.library_busy and not app.library_progress)
        self.refresh_messages()
        actions = ft.Row([
            button("Launch", lambda e: app.start_library(app.cfg["library"]["default_source"]), c, ft.Icons.PLAY_ARROW_ROUNDED, primary=True),
            button("Choose Directory", self.choose, c, ft.Icons.FOLDER_OPEN_OUTLINED),
            ft.Container(expand=True),
            ft.IconButton(ft.Icons.STOP_CIRCLE_OUTLINED, icon_color=c["muted"], tooltip="Stop this AI session", on_click=lambda e: app.stop_library()),
            ft.PopupMenuButton(icon=ft.Icons.MORE_HORIZ, icon_color=c["muted"], items=[
                ft.PopupMenuItem(text="Update document index", on_click=self.rebuild_index),
                ft.PopupMenuItem(text="Clear conversation", on_click=self.clear)])], spacing=10)
        chat = panel(ft.Column([
            ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, color=c["accent"], size=20),
                    ft.Text("Local librarian", size=17, color=c["text"], weight=ft.FontWeight.W_600),
                    ft.Container(expand=True), self.status]), self.summary,
            ft.Row([ft.Container(self.modes, bgcolor=c["bg"], border_radius=9, padding=3), ft.Container(expand=True), self.source_filter], spacing=10),
            ft.Divider(height=1, color=c["border"]), self.messages, self.progress,
            ft.Row([self.input, self.send], spacing=12),
            label("Find passages for direct results, or ask AI for an answer with evidence. After adding documents, use Update document index.", c, 11),
            ft.ExpansionTile(title=label("Session activity", c, 11), controls=[self.activity], initially_expanded=False,
                             collapsed_icon_color=c["dim"], icon_color=c["accent"])], expand=True, spacing=13), c, expand=True)
        self.content = ft.Column([
            heading("AI Library", "Your documents. Your models. On your computer.", c),
            actions, label("Launch uses: " + app.cfg["library"]["default_source"], c, 11), self.custom_panel, self.processing_panel, chat],
            expand=True, spacing=14)
        self.sync_controls()

    def summary_text(self):
        app = self.app
        if app.library_ready:
            return f"{app.library_documents:,} documents · {app.library_chunks:,} searchable passages · {app.library_source}"
        if app.library_progress:
            return "Your documents are still being processed. Search and chat will unlock when the library is ready."
        return "Launch your saved library or choose a processed-file directory to begin."

    def choose(self, _):
        self.custom_panel.visible = not self.custom_panel.visible
        self.custom_panel.update()

    def mode(self, value):
        self.app.library_mode = value
        self.sync_controls()
        self.refresh_messages()
        self.content.update()

    def filter_source(self, event):
        self.app.library_filter = "" if event.control.value == "__all__" else event.control.value

    def sync_controls(self):
        app, c = self.app, self.c
        self.modes.controls = [button(title, lambda e, v=value: self.mode(v), c, primary=app.library_mode == value)
                               for value, title in [("ask", "Ask AI"), ("search", "Find passages")]]
        self.source_filter.options = [ft.dropdown.Option("__all__", "All documents"), *[ft.dropdown.Option(s) for s in app.library_sources]]
        self.source_filter.value = app.library_filter or "__all__"
        self.source_filter.disabled = not app.library_ready or app.library_busy
        self.input.hint_text = "Find a name, phrase, or topic…" if app.library_mode == "search" else "Ask a question about your library…"
        self.send.icon = ft.Icons.SEARCH if app.library_mode == "search" else ft.Icons.ARROW_UPWARD_ROUNDED
        self.send.tooltip = "Search passages" if app.library_mode == "search" else "Ask library"
        event = app.library_progress
        self.processing_panel.visible = bool(event)
        if event:
            total, completed = event.get("total"), event.get("completed", 0)
            self.processing_title.value = event.get("message", "Processing documents")
            self.processing_bar.value = min(1, completed / total) if total else None
            phase = event.get("phase")
            unit = "documents" if phase == "reading" else "passages"
            detail = f"{completed:,} / {total:,} {unit} · {completed / total:.0%}" if total else "This stage is still running. Keep the session open."
            if event.get("skipped"):
                detail += f" · {event['skipped']} skipped (see activity)"
            self.processing_detail.value = detail
            self.processing_file.value = event.get("current_file", "Your original documents remain unchanged.")

    def ask(self, _):
        question = (self.input.value or "").strip()
        if not question:
            return
        if self.app.library_busy or not self.app.library_ready:
            self.app.notify("Wait for the library to be ready.")
            return
        try:
            is_search = self.app.library_mode == "search"
            self.app.library_session.send({"command": "search" if is_search else "ask", "question": question,
                                           "source": self.app.library_filter or None})
            if not is_search:
                self.app.library_messages.append({"role": "user", "text": question})
            else:
                self.app.library_results = []
                self.app.library_search_query = question
            self.app.library_busy = True
            if not is_search:
                self.input.value = ""
            self.refresh_messages()
            self.refresh()
        except (ValueError, OSError) as error:
            self.app.notify(str(error), True)

    def rebuild_index(self, _):
        source = self.app.library_source or self.app.cfg["library"]["default_source"]
        self.app.confirm("Update document index?", "New and changed documents will be embedded. The previous index is kept as a backup. Stop any running session first.",
                         lambda: self.app.start_library(source, rebuild=True))

    def clear(self, _):
        if self.app.library_busy:
            self.app.notify("Wait for the current answer before clearing the conversation.")
            return
        self.app.library_messages.clear()
        self.app.library_results, self.app.library_search_query = [], ""
        self.refresh_messages()
        self.messages.update()

    def source_dialog(self, source, number):
        c = self.c
        dialog = ft.AlertDialog(title=ft.Text(f"[{number}] {source['source']}", color=c["text"]),
                                content=ft.Container(ft.Column([label(f"Passage {source['chunk'] + 1} · {source.get('match', 'semantic')} match", c),
                                                                ft.Text(source["text"], size=13, color=c["text"], selectable=True)],
                                                               scroll=ft.ScrollMode.AUTO), width=620, height=450), bgcolor=c["surface"])
        dialog.actions = [ft.TextButton("Close", on_click=lambda e: self.app.page.close(dialog))]
        self.app.page.open(dialog)

    def refresh_messages(self):
        c = self.c
        if self.app.library_mode == "search":
            self.refresh_results()
            return
        controls = []
        for message in self.app.library_messages:
            user = message["role"] == "user"
            content = [label("YOU" if user else "LIBRARIAN", c, 10),
                       ft.Markdown(message["text"], selectable=True, auto_follow_links=False,
                                   md_style_sheet=ft.MarkdownStyleSheet(p_text_style=ft.TextStyle(size=14, color=c["text"], height=1.6)))]
            if message.get("sources"):
                content += [ft.Row([ft.TextButton(f"[{i}] {s['source']}", on_click=lambda e, src=s, num=i: self.source_dialog(src, num),
                                                  style=ft.ButtonStyle(color=c["cyan"]))
                                    for i, s in enumerate(message["sources"], 1)], wrap=True, spacing=4)]
            controls.append(ft.Container(ft.Column(content, spacing=9), bgcolor=c["raised"] if user else c["bg"],
                                         border_radius=12, padding=18))
        if not controls:
            controls = [ft.Container(ft.Column([
                ft.Icon(ft.Icons.AUTO_AWESOME, size=45, color=c["accent"]),
                ft.Text("A conversation with your library.", size=22, color=c["text"], text_align=ft.TextAlign.CENTER),
                label("Search across your books and processed documents, then follow the evidence.", c, 13),
                ft.Row([pill("Local models", c["accent"], c), pill("Source citations", c["cyan"], c),
                        pill("Existing indexes supported", c["success"], c)], alignment=ft.MainAxisAlignment.CENTER, wrap=True)],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=18), padding=ft.padding.symmetric(vertical=65, horizontal=20))]
        self.messages.controls = controls

    def refresh_results(self):
        app, c = self.app, self.c
        controls = []
        if app.library_search_query:
            controls.append(ft.Row([ft.Text(f"Results for “{app.library_search_query}”", size=15, color=c["text"], expand=True, max_lines=2),
                                    pill(f"{len(app.library_results)} passages · {app.library_search_seconds:.2f}s", c["cyan"], c)]))
        for i, result in enumerate(app.library_results, 1):
            title = re.split(r"[/\\]", result["source"])[-1]
            excerpt = result.get("snippet", result["text"][:300])
            words = tokens(app.library_search_query)
            pattern = re.compile("(" + "|".join(re.escape(w) for w in words) + ")", re.I) if words else None
            spans = [ft.TextSpan(part, ft.TextStyle(color=c["accent"], weight=ft.FontWeight.W_600) if pattern and pattern.fullmatch(part) else None)
                     for part in pattern.split(excerpt)] if pattern else [ft.TextSpan(excerpt)]
            controls.append(panel(ft.Column([
                ft.Row([pill(f"{i:02}", c["accent"], c), ft.Text(title, size=14, color=c["text"], weight=ft.FontWeight.W_600, expand=True,
                                                                              max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                        pill(result.get("match", "semantic").title(), c["cyan"], c), ft.Icon(ft.Icons.NORTH_EAST, color=c["dim"], size=17)], spacing=10),
                ft.Text(spans=spans, size=13, color=c["muted"], max_lines=4, overflow=ft.TextOverflow.ELLIPSIS),
                ft.Text(f"{result['source']} · Passage {result['chunk'] + 1}", size=10, color=c["dim"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=10),
                c, padding=16, ink=True, on_click=lambda e, s=result, n=i: self.source_dialog(s, n)))
        if not app.library_results and not app.library_busy:
            controls.append(ft.Container(ft.Column([ft.Icon(ft.Icons.MANAGE_SEARCH, size=43, color=c["accent"]),
                                                     ft.Text("Find the passage you need." if not app.library_search_query else "No matching passages.", size=22, color=c["text"]),
                                                     label("Search a topic or exact name. Narrow results with the source filter.", c)],
                                                    spacing=15, horizontal_alignment=ft.CrossAxisAlignment.CENTER), padding=40))
        self.messages.controls = controls

    def refresh(self):
        app = self.app
        self.status.value, self.summary.value = app.library_status, self.summary_text()
        self.input.disabled = self.send.disabled = not app.library_ready or app.library_busy
        self.progress.visible = app.library_busy and not app.library_progress
        self.sync_controls()
        self.activity.value = "\n".join(app.library_logs[-20:])
        self.content.update()
