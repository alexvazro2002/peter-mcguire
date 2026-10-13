"""Peter's observatory, with live links to the existing workspace features."""
from datetime import date, datetime, time, timedelta

import flet as ft

from app.theme import button, heading, label, panel
from app.tools import TOOLS
from app.views.peter import PeterOrb


def focus_groups(entries, now):
    today = now.date().isoformat()
    overdue, current, unscheduled, future = [], [], [], []
    for item in sorted(entries, key=lambda e: (e.get("date") or "9999", e.get("time") or "", e["title"].casefold())):
        if item.get("done"):
            continue
        day = item.get("date")
        if not day:
            unscheduled.append(item)
            continue
        missed = day < today or (day == today and item.get("time") and time.fromisoformat(item["time"]) < now.time())
        if item["kind"] == "task" and missed:
            overdue.append(item)
        elif day == today:
            current.append(item)
        elif day > today:
            future.append(item)
    return overdue, current, unscheduled, future


def lateness_text(item, now):
    day = date.fromisoformat(item["date"])
    if not item.get("time"):
        days = (now.date() - day).days
        amount, unit = (days // 7, "week") if days >= 7 else (days, "day")
    else:
        due = datetime.combine(day, time.fromisoformat(item["time"]))
        seconds = max(0, (now.replace(tzinfo=None) - due).total_seconds())
        if seconds < 60:
            return "Just overdue"
        amount, unit = next((int(seconds // divisor), unit) for divisor, unit in
                            [(604800, "week"), (86400, "day"), (3600, "hour"), (60, "minute")] if seconds >= divisor)
    return f"{amount} {unit}{'' if amount == 1 else 's'} overdue"


class PeterHomeView:
    def __init__(self, app):
        self.app, self.c = app, app.c
        c, now = self.c, datetime.now()
        height = getattr(app.page, "height", None) or getattr(app.page.window, "height", 900) or 900
        compact = height < 780
        app.home_focus_minute = now.replace(second=0, microsecond=0)
        self.orb = PeterOrb(app, size=160 if compact else 208)
        self.state = ft.Text("", size=10, color=c["cyan"], weight=ft.FontWeight.W_600,
                             text_align=ft.TextAlign.CENTER)
        self.state_icon = ft.Icon(ft.Icons.RADIO_BUTTON_CHECKED, size=13, color=c['cyan'])
        self.status = ft.Text("", size=12, color=c["muted"], text_align=ft.TextAlign.CENTER,
                              max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        self.model_status = label(app.peter_model_status, c, 10)
        self.conversation = ft.ListView(expand=True, spacing=10, auto_scroll=True)
        self._message_signature = None
        self.input = ft.TextField(value=app.assistant_draft, hint_text="Ask Peter anything…",
                                 hint_style=ft.TextStyle(color=c["dim"]), color=c["text"],
                                 bgcolor=c["bg"], border_color=c["border"], border_radius=12,
                                 focused_border_color=c["cyan"], text_size=13, expand=True,
                                 max_length=8000, counter_text="", on_change=self.draft, on_submit=self.send,
                                 content_padding=ft.padding.symmetric(horizontal=14, vertical=14))
        self.send_button = ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip="Send to Peter",
                                         icon_color=c["accent_text"], bgcolor=c["accent"], on_click=self.send)
        self.listen = button("Speak to Peter", lambda _: app.toggle_peter_listen(), c, ft.Icons.MIC_NONE_ROUNDED)
        self.stop = ft.IconButton(ft.Icons.STOP_CIRCLE_OUTLINED, tooltip="Stop speech or current request",
                                  icon_color=c["muted"], on_click=lambda _: app.cancel_peter())
        center = panel(ft.Column([
            *([] if compact else [ft.Text("PETER", size=12, color=c["text"], weight=ft.FontWeight.W_600)]),
            ft.Container(self.orb, alignment=ft.alignment.center),
            ft.Row([self.state_icon, self.state], alignment=ft.MainAxisAlignment.CENTER, spacing=6), self.status,
            ft.Container(self.conversation, expand=True, padding=ft.padding.symmetric(horizontal=4, vertical=6)),
            ft.Row([self.listen, self.stop], spacing=4, alignment=ft.MainAxisAlignment.CENTER),
            ft.Row([self.input, self.send_button], spacing=8),
            ft.TextButton("Open conversation", on_click=lambda _: app.navigate("assistant"),
                          height=30 if compact else 40,
                          style=ft.ButtonStyle(color=c["muted"])),
            *([] if compact else [self.model_status])
        ], expand=True, spacing=5 if compact else 8, horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            c, padding=12 if compact else 18, expand=5)
        self.content = ft.Column([
            ft.Row([ft.Column([ft.Text("Your command center.", size=25, weight=ft.FontWeight.W_500, color=c["text"]),
                               label(now.strftime("%A, %d %B %Y"), c, 12)], spacing=6),
                    ft.Container(expand=True),
                    ft.Container(ft.Row([ft.Icon(ft.Icons.LOCK_OUTLINE, color=c["cyan"], size=13),
                                         label("ON DEVICE", c, 10)], spacing=6),
                                 padding=10, border_radius=8, bgcolor=c["surface"])], spacing=10),
            ft.Row([self.focus(now), center, self.quick_access()], expand=True, spacing=14,
                   vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        ], expand=True, spacing=20)
        self.refresh(update=False)

    def focus(self, now):
        app, c = self.app, self.c
        groups, rows = focus_groups(app.calendar.entries(), now), []
        for title, items in zip(("Overdue", "Today", "Unscheduled", "Upcoming"), groups):
            if not items:
                continue
            color = c["danger"] if title == "Overdue" else c["text"]
            rows.append(ft.Text(f"{title.upper()} · {len(items)}", size=10, weight=ft.FontWeight.W_600, color=color))
            for item in items:
                scheduled = item.get("date")
                detail = lateness_text(item, now) if title == "Overdue" else (
                    f"{date.fromisoformat(scheduled):%a %d %b} · {item.get('time') or 'All day'}" if scheduled else "Unscheduled")
                rows.append(ft.Container(ft.Column([
                    ft.Text(item["title"], size=12, color=color, max_lines=2, overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(detail, size=10, color=c["danger"] if title == "Overdue" else c["muted"])
                ], spacing=4), padding=10, bgcolor=c["bg"], border_radius=8, ink=True,
                    on_click=(lambda _, d=scheduled: app.open_calendar(date.fromisoformat(d))) if scheduled else lambda _: app.open_unscheduled()))
        start = now.date() - timedelta(days=now.weekday())
        week = ft.Row([ft.Container(ft.Column([
            label((start + timedelta(days=i)).strftime("%a")[0], c, 9),
            ft.Text(str((start + timedelta(days=i)).day), size=12,
                    color=c["cyan"] if start + timedelta(days=i) == now.date() else c["muted"])
        ], horizontal_alignment=ft.CrossAxisAlignment.CENTER, spacing=4), expand=True,
            padding=ft.padding.symmetric(vertical=8), border_radius=7,
            bgcolor=c["raised"] if start + timedelta(days=i) == now.date() else None,
            on_click=lambda _, d=start + timedelta(days=i): app.open_calendar(d), ink=True)
            for i in range(7)], spacing=2)
        return panel(ft.Column([
            ft.Row([ft.Icon(ft.Icons.CALENDAR_MONTH_OUTLINED, color=c["accent"], size=18),
                    ft.Text("Today's focus", size=14, color=c["text"], expand=True),
                    ft.IconButton(ft.Icons.ADD, icon_color=c["muted"], icon_size=17,
                                  tooltip="Add task", on_click=lambda _: app.open_calendar())], spacing=8),
            week, ft.Divider(height=1, color=c["border"]),
            ft.ListView(rows or [label("A little room in your day. Add a task to begin.", c)], expand=True, spacing=8),
            ft.TextButton("Open calendar", on_click=lambda _: app.navigate("tasks"),
                          style=ft.ButtonStyle(color=c["accent"]))
        ], expand=True, spacing=14), c, padding=14, expand=3)

    def quick_access(self):
        app, c = self.app, self.c
        names = sorted(app.vault.names(), key=lambda n: app.cfg["notes_last_used"].get(n, 0), reverse=True)
        notes = [ft.TextButton(name, icon=ft.Icons.EDIT_NOTE_OUTLINED,
                              on_click=lambda _, n=name: app.open_note(n), style=ft.ButtonStyle(color=c["muted"]))
                 for name in names[:3]] or [label("A fresh notes workspace.", c, 11)]
        tools = sorted(TOOLS, key=lambda tool: (-app.cfg["usage"].get(tool.key, 0), tool.title.casefold()))[:4]
        try:
            memory_count = len(app.assistant.memory.list())
            memory_status = f"{memory_count} memor{'y' if memory_count == 1 else 'ies'} shared with Peter"
        except (OSError, ValueError):
            memory_status = "Memory needs attention"
        sections = [
            ft.Row([ft.Icon(ft.Icons.EDIT_NOTE_OUTLINED, color=c["cyan"], size=18),
                    ft.Text("Notes", size=14, color=c["text"], expand=True),
                    ft.IconButton(ft.Icons.ADD, tooltip="New note", icon_size=17, icon_color=c["muted"],
                                  on_click=lambda _: app.new_note())], spacing=8),
            *notes, ft.TextButton(f"Open notes · {len(names)}", on_click=lambda _: app.navigate("notes"),
                                 style=ft.ButtonStyle(color=c["cyan"])),
            ft.Divider(color=c["border"]),
            ft.Text("TOOLS", size=10, color=c["dim"], weight=ft.FontWeight.W_600),
            *[ft.Container(ft.Row([ft.Icon(tool.icon, size=17, color=c["accent"]),
                                   ft.Text(tool.title, color=c["muted"], size=12, expand=True,
                                           max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)], spacing=10),
                           on_click=lambda _, t=tool: app.open_tool(t), ink=True, padding=10,
                           bgcolor=c["bg"], border_radius=8) for tool in tools],
            ft.TextButton("All tools", on_click=lambda _: app.navigate("tools"), style=ft.ButtonStyle(color=c["accent"])),
            ft.Divider(color=c["border"]),
            ft.Container(ft.Column([
                ft.Row([ft.Icon(ft.Icons.PSYCHOLOGY_OUTLINED, size=18, color=c["cyan"]),
                        ft.Text("Memory", size=14, color=c["text"])], spacing=8), label(memory_status, c, 11)
            ], spacing=8), padding=12, bgcolor=c["bg"], border_radius=8, ink=True,
                on_click=lambda _: app.navigate("memory")),
            ft.TextButton("AI Library", icon=ft.Icons.AUTO_AWESOME_OUTLINED, on_click=lambda _: app.navigate("library"),
                          style=ft.ButtonStyle(color=c["muted"]))
        ]
        return panel(ft.ListView(sections, expand=True, spacing=10), c, padding=14, expand=3)

    def draft(self, event):
        self.app.assistant_draft = event.control.value or ""

    def send(self, _):
        text = (self.input.value or "").strip()
        if text and self.app.submit_peter(text):
            self.input.value = ""
            self.refresh()

    def refresh_conversation(self):
        """Keep typed and transcribed messages visible, including in a small window."""
        messages = self.app.assistant.messages[-20:]
        signature = tuple((message["role"], message["text"]) for message in messages)
        if signature == self._message_signature:
            return
        self._message_signature = signature
        c = self.c
        self.conversation.controls = [ft.Container(ft.Column([
            ft.Text("YOU" if message["role"] == "user" else "PETER", size=9,
                    weight=ft.FontWeight.W_600,
                    color=c["cyan"] if message["role"] == "user" else c["dim"]),
            ft.Text(message["text"], size=12, color=c["text"], selectable=True)
        ], spacing=4), padding=10, border_radius=9,
            bgcolor=c["bg"] if message["role"] == "user" else c["raised"])
            for message in messages]
        if not messages:
            self.conversation.controls = [ft.Text("Your conversation appears here. Type below or speak to Peter.",
                                                 size=12, color=c["muted"], text_align=ft.TextAlign.CENTER)]

    def refresh(self, update=True):
        app, state = self.app, self.app.peter_state
        self.orb.set_state(state)
        self.state.value = {"idle": "STANDBY", "listening": "LISTENING", "thinking": "THINKING",
                            "speaking": "TALKING", "error": "NEEDS ATTENTION"}.get(state, "STANDBY")
        self.state.color = self.c["danger"] if state == "error" else self.c["violet"] if state == "thinking" else self.c["cyan"]
        self.state_icon.name = {"idle": ft.Icons.RADIO_BUTTON_CHECKED, "listening": ft.Icons.MIC_NONE_ROUNDED,
                                "thinking": ft.Icons.AUTO_AWESOME_OUTLINED, "speaking": ft.Icons.VOLUME_UP_ROUNDED,
                                "error": ft.Icons.ERROR_OUTLINE}.get(state, ft.Icons.RADIO_BUTTON_CHECKED)
        self.state_icon.color = self.state.color
        self.status.value = app.assistant_status if app.assistant.busy else app.peter_status
        self.model_status.value = app.peter_model_status
        self.listen.text = "Finish listening" if state == "listening" else "Speak to Peter"
        self.listen.disabled = app.assistant.busy or not app.cfg.get("peter", {}).get("enabled", True)
        self.input.disabled = self.send_button.disabled = app.assistant.busy
        self.refresh_conversation()
        if update:
            self.content.update()


def build_home(app):
    app.home_view = PeterHomeView(app)
    return app.home_view.content
