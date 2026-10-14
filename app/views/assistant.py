"""Persistent workspace chat using the same controls and palette as the Library."""
from datetime import date, datetime

import flet as ft

from app.theme import button, field, heading, label, pill
from app.views.model_picker import choose_model
from app.speech import conversation_parts


class AssistantView:
    def __init__(self, app):
        self.app, self.c = app, app.c
        c = self.c
        self.messages = ft.ListView(expand=True, spacing=16, auto_scroll=True)
        self.status = ft.Text(app.assistant_status, color=c["muted"], size=12, expand=True)
        self.progress = ft.ProgressBar(color=c["accent"], bgcolor=c["raised"], height=3, visible=app.assistant.busy)
        self.input = field(c, value=app.assistant_draft, hint_text="Message Peter…", multiline=True,
                           min_lines=1, max_lines=4, shift_enter=True, on_submit=self.send, on_change=self.draft,
                           expand=True, max_length=8000, disabled=app.assistant.busy)
        self.send_button = ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip="Send message", bgcolor=c["accent"],
                                         icon_color=c["accent_text"], on_click=self.send, disabled=app.assistant.busy)
        self.cancel_button = ft.IconButton(ft.Icons.STOP_CIRCLE_OUTLINED, tooltip="Cancel pending request", icon_color=c["muted"],
                                           on_click=self.cancel, visible=app.assistant.busy)
        actions = [ft.IconButton(ft.Icons.MEMORY_OUTLINED, tooltip="Choose AI model", icon_color=c["muted"],
                                 disabled=app.assistant.busy, on_click=lambda e: choose_model(app, "Peter", lambda: None)),
                   ft.IconButton(ft.Icons.HISTORY, tooltip="Peter's change history", icon_color=c["muted"], on_click=self.history),
                   ft.IconButton(ft.Icons.ADD_COMMENT_OUTLINED, tooltip="New conversation", icon_color=c["muted"], on_click=self.clear)]
        self.content = ft.Column([
            heading("Peter McGuire", "A conversation with your workspace.", c, actions),
            ft.Row([pill("Notes", c["accent"], c), pill("Calendar & tasks", c["cyan"], c),
                    pill("Lists, memories & history", c["success"], c),
                    pill("Repository (read-only)", c["accent"], c),
                    label("Same local model as AI Library", c, 11)], wrap=True, spacing=8),
            ft.Divider(height=1, color=c["border"]), self.messages,
            self.progress, ft.Row([ft.Icon(ft.Icons.AUTO_AWESOME_OUTLINED, size=14, color=c["accent"]), self.status], spacing=8),
            ft.Row([self.input, self.cancel_button, self.send_button], spacing=10)], expand=True, spacing=14)
        self.refresh_messages()

    def draft(self, event):
        self.app.assistant_draft = event.control.value or ""

    def send(self, _):
        text = (self.input.value or "").strip()
        if not text:
            return
        if self.app.submit_peter(text):
            self.input.value = ""
            self.refresh_messages()
            self.refresh()

    def cancel(self, _):
        self.app.cancel_peter()
        self.app.assistant_status = "Cancelling; waiting for the current model response"
        self.refresh()

    def source(self, source):
        if source["kind"] in {"web", "email"}:
            try:
                from app.integrations import source_url
                self.app.page.launch_url(source_url(source))
            except ValueError as error:
                self.app.notify(str(error), True)
        elif source["kind"] == "repository":
            try:
                from app.assistant.repository import Repository
                result = Repository().read(source["path"], limit=8000)
                dialog = ft.AlertDialog(title=ft.Text(result["path"], color=self.c["text"]), bgcolor=self.c["surface"])
                dialog.content = ft.Container(ft.ListView([ft.Text(result["content"], font_family="DejaVu Sans Mono", size=12,
                                                                     selectable=True, color=self.c["text"])], expand=True), width=700, height=420)
                dialog.actions = [ft.TextButton("Close", on_click=lambda e: self.app.page.close(dialog))]
                self.app.page.open(dialog)
            except (OSError, ValueError) as error:
                self.app.notify(str(error), True)
        elif source["kind"] == "memory":
            self.app.navigate("memory")
        elif source["kind"] == "note":
            if source["name"] not in self.app.vault.names():
                self.app.notify("That note is no longer present.", True)
            else:
                self.app.open_note(source["name"])
        elif source.get("date"):
            self.app.open_calendar(date.fromisoformat(source["date"]))
        else:
            self.app.open_unscheduled()

    def undo(self, operation_id):
        try:
            self.app.assistant.undo(operation_id)
            self.app.assistant_data_changed()
            self.refresh_messages()
            if self.app.view == "assistant":
                self.refresh()
            self.app.notify("Peter's changes undone.")
        except (OSError, ValueError, RuntimeError) as error:
            self.app.notify(str(error), True)

    def history(self, _):
        try:
            records = self.app.assistant.history.recent()
            c = self.c
            dialog = ft.AlertDialog(title=ft.Text("Peter's changes", color=c["text"]), bgcolor=c["surface"])
            def undo_and_close(record):
                self.app.page.close(dialog)
                self.undo(record["id"])
            rows = []
            for record in records:
                rows.append(ft.Container(ft.Column([
                    ft.Row([label(datetime.fromisoformat(record["created_at"]).strftime("%d %b %Y, %H:%M UTC"), c, 11),
                            ft.Container(expand=True), label(record["status"].replace("_", " "), c, 11),
                            ft.IconButton(ft.Icons.UNDO, tooltip="Undo this entire operation", icon_color=c["accent"],
                                          disabled=self.app.assistant.busy or record["status"] != "committed",
                                          on_click=lambda e, r=record: undo_and_close(r))]),
                    *[ft.Text(summary, color=c["text"], size=12) for summary in record["summaries"]]], spacing=6),
                                         padding=12, border=ft.border.only(bottom=ft.BorderSide(1, c["border"]))))
            dialog.content = ft.Container(ft.ListView(rows or [label("No changes yet.", c)], spacing=12), width=620, height=420)
            dialog.actions = [ft.TextButton("Close", on_click=lambda e: self.app.page.close(dialog))]
            self.app.page.open(dialog)
        except (OSError, ValueError) as error:
            self.app.notify(str(error), True)

    def clear(self, _):
        def clear():
            self.app.assistant.clear()
            self.refresh_messages()
            self.refresh()
        self.app.confirm("Start a new conversation?", "This clears chat only. Your notes, tasks and change history remain intact.", clear)

    def refresh_messages(self):
        c, controls = self.c, []
        committed = {r["id"] for r in self.app.assistant.history.recent(limit=200) if r["status"] == "committed"} if not self.app.assistant.warning else set()
        for message in conversation_parts(self.app.assistant.messages):
            user = message["role"] == "user"
            contents = [label("YOU" if user else "PETER", c, 10),
                        ft.Markdown(message["text"], selectable=True, auto_follow_links=False,
                                    md_style_sheet=ft.MarkdownStyleSheet(p_text_style=ft.TextStyle(size=14, color=c["text"], height=1.6)))]
            if message.get("sources"):
                contents.append(ft.Row([ft.TextButton("Memory" if s["kind"] == "memory" else s.get("title") or s.get("name") or s.get("path") or s.get("date") or "Unscheduled",
                                                      icon=ft.Icons.NORTH_EAST, on_click=lambda e, src=s: self.source(src),
                                                      style=ft.ButtonStyle(color=c["cyan"])) for s in message["sources"]], wrap=True, spacing=4))
            operation_id = message.get("operation_id")
            if operation_id and operation_id in committed:
                count = len(message.get("changes", []))
                contents.append(ft.Row([pill(f"{count} change{'s' if count != 1 else ''}" if count else "Undo recorded", c["success"], c),
                                        ft.IconButton(ft.Icons.UNDO, tooltip="Undo all changes from this message", icon_color=c["muted"],
                                                      on_click=lambda e, op=operation_id: self.undo(op), disabled=self.app.assistant.busy)], spacing=10))
            controls.append(ft.Container(ft.Column(contents, spacing=9), padding=18, border_radius=8,
                                         bgcolor=c["raised"] if user else c["surface"]))
        if not controls:
            controls = [ft.Container(ft.Column([
                ft.Icon(ft.Icons.FORUM_OUTLINED, color=c["accent"], size=38),
                ft.Text("What’s on your mind?", size=24, color=c["text"], weight=ft.FontWeight.W_600),
                ft.Row([button("What do I have planned for Sunday?", lambda e: self.example("Show me everything I have planned for Sunday."), c),
                        button("Find my watchlist", lambda e: self.example("What movies do I want to watch?"), c)], wrap=True, spacing=8)],
                                                  spacing=18, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
                                     height=360, alignment=ft.alignment.center, padding=30)]
        if self.app.assistant.warning:
            controls.append(ft.Text(self.app.assistant.warning, color=c["danger"], selectable=True))
        self.messages.controls = controls

    def example(self, text):
        self.input.value = self.app.assistant_draft = text
        self.input.update()
        self.input.focus()

    def refresh(self):
        busy = self.app.assistant.busy
        self.progress.visible, self.input.disabled, self.send_button.disabled, self.cancel_button.visible = busy, busy, busy, busy
        self.status.value = self.app.assistant_status
        self.content.update()
