"""Three calendar views over one local store of tasks and notes."""
from datetime import date, datetime, timedelta
import calendar
from collections import defaultdict
import flet as ft
from app.calendar_storage import month_days, shift_month
from app.theme import button, field, heading, label, panel, pill


class TasksView:
    def __init__(self, app):
        self.app, self.c, self.query, self.show_done = app, app.c, "", True
        self.body = ft.Container(expand=True)
        self.period = ft.Text(size=19, weight=ft.FontWeight.W_600, color=self.c["text"])
        self.tabs = ft.Row(spacing=3)
        self.search = field(self.c, hint_text="Filter this view…", prefix_icon=ft.Icons.SEARCH, width=190, on_change=self.filter)
        self.unscheduled_button = button("Unscheduled", self.unscheduled, self.c, ft.Icons.INBOX_OUTLINED)
        self.content = ft.Column([
            heading("Calendar", "Your tasks and notes, in the rhythm of your day.", self.c,
                    [self.search, self.unscheduled_button, button("New item", lambda e: self.edit(), self.c, ft.Icons.ADD, primary=True)]),
            ft.Row([ft.Container(self.tabs, bgcolor=self.c["surface"], border_radius=10, padding=3), ft.Container(expand=True),
                    ft.IconButton(ft.Icons.CHEVRON_LEFT, tooltip="Previous period", on_click=lambda e: self.move(-1), icon_color=self.c["muted"]),
                    ft.Container(self.period, ink=True, tooltip="Jump to a date", on_click=lambda e: self.pick_date()),
                    ft.IconButton(ft.Icons.CHEVRON_RIGHT, tooltip="Next period", on_click=lambda e: self.move(1), icon_color=self.c["muted"]),
                    button("Today", self.today, self.c)], spacing=8), self.body,
            ft.Row([pill("Tasks", self.c["accent"], self.c), pill("Notes", self.c["cyan"], self.c),
                    label("Click a day for its agenda · Times are local to your computer", self.c, 11), ft.Container(expand=True),
                    ft.Checkbox(label="Show completed", value=True, on_change=self.completed_filter)], spacing=10)
        ], expand=True, spacing=16)
        self.refresh()

    @property
    def day(self):
        return self.app.calendar_day

    def items(self, day):
        return self.by_date.get(day.isoformat(), [])

    def refresh(self, update=False):
        c, mode, day = self.c, self.app.calendar_mode, self.day
        self.by_date = defaultdict(list)
        for item in self.app.calendar.entries(query=self.query, show_done=self.show_done):
            self.by_date[item.get("date")].append(item)
        self.tabs.controls = [button(title, lambda e, m=key: self.mode(m), c, primary=mode == key)
                              for key, title in [("monthly", "Monthly"), ("weekly", "Weekly"), ("daily", "Daily")]]
        self.unscheduled_button.text = f"Unscheduled · {len(self.app.calendar.entries(unscheduled=True, show_done=False))}"
        if mode == "monthly":
            self.period.value, self.body.content = day.strftime("%B %Y"), self.monthly()
        elif mode == "weekly":
            start = day - timedelta(days=day.weekday())
            self.period.value = f"{start:%d %b} – {start + timedelta(days=6):%d %b %Y}"
            self.body.content = self.weekly(start)
        else:
            self.period.value, self.body.content = day.strftime("%d %B %Y"), self.daily()
        if update:
            self.content.update()

    def mode(self, mode):
        self.app.calendar_mode = mode
        self.refresh(True)

    def move(self, offset):
        self.app.calendar_day = shift_month(self.day, offset) if self.app.calendar_mode == "monthly" else self.day + timedelta(days=offset * (7 if self.app.calendar_mode == "weekly" else 1))
        self.refresh(True)

    def today(self, _):
        self.app.calendar_day = date.today()
        self.refresh(True)

    def pick_date(self, target=None):
        chosen = self.day
        if target and target.value:
            try:
                chosen = date.fromisoformat(target.value)
            except ValueError:
                pass
        chosen = min(max(chosen, date(1900, 1, 1)), date(2200, 12, 31))
        def select(event):
            selected = event.control.value.date()
            if target:
                target.value = selected.isoformat()
                target.update()
            else:
                self.app.calendar_day = selected
                self.refresh(True)
        picker = ft.DatePicker(value=datetime.combine(chosen, datetime.min.time()), first_date=datetime(1900, 1, 1),
                               last_date=datetime(2200, 12, 31), on_change=select)
        self.app.page.open(picker)

    def open_day(self, day):
        self.app.calendar_day, self.app.calendar_mode = day, "daily"
        self.refresh(True)

    def filter(self, event):
        self.query = event.control.value or ""
        self.refresh(True)

    def completed_filter(self, event):
        self.show_done = event.control.value
        self.refresh(True)

    def compact(self, item, weekly=False):
        c = self.c
        color = c["cyan"] if item["kind"] == "note" else c["accent"]
        if item.get("done"):
            color = c["dim"]
        title = ft.Text(item["title"], size=12 if weekly else 11, color=c["muted"] if item.get("done") else c["text"],
                        max_lines=2 if weekly else 1, overflow=ft.TextOverflow.ELLIPSIS,
                        style=ft.TextStyle(decoration=ft.TextDecoration.LINE_THROUGH if item.get("done") else None))
        parts = [ft.Row([ft.Icon(ft.Icons.CHECK if item.get("done") else ft.Icons.CIRCLE, size=8, color=color), ft.Container(title, expand=True)], spacing=5)]
        if weekly:
            parts = [label(item.get("time") or "All day", c, 10), *parts]
            if item.get("details"):
                parts.append(ft.Text(item["details"], size=10, color=c["muted"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS))
            if item.get("note"):
                parts.append(ft.Text("↗ " + item["note"], size=10, color=c["cyan"], max_lines=1, overflow=ft.TextOverflow.ELLIPSIS))
        elif item.get("time"):
            title.value = item["time"] + "  " + title.value
        return ft.Container(ft.Column(parts, spacing=5), bgcolor=ft.Colors.with_opacity(.07, color),
                            border=ft.border.only(left=ft.BorderSide(2, color)), padding=7 if weekly else 4,
                            border_radius=5, tooltip=item["title"], ink=True, on_click=lambda e: self.edit(item))

    def monthly(self):
        c, days = self.c, month_days(self.day)
        rows = [ft.Row([ft.Container(label(name, c, 11), expand=True, alignment=ft.alignment.center)
                        for name in ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]], height=25)]
        week_count = (self.day.replace(day=1).weekday() + calendar.monthrange(self.day.year, self.day.month)[1] + 6) // 7
        cell_height = ((getattr(self.app.page, "height", None) or 900) - 270) / week_count
        max_items = max(0, min(3, int((cell_height - 55) / 26)))
        for week in range(week_count):
            cells = []
            for day in days[week * 7:week * 7 + 7]:
                entries = self.items(day)
                is_today, current = day == date.today(), day.month == self.day.month
                badge = ft.Container(ft.Text(str(day.day), size=12, color=c["accent_text"] if is_today else c["text"] if current else c["dim"], weight=ft.FontWeight.W_600),
                                     width=27, height=27, border_radius=8, bgcolor=c["accent"] if is_today else None, alignment=ft.alignment.center)
                items = [ft.Row([badge, ft.Container(expand=True),
                                 ft.Text(f"+{len(entries) - max_items}", size=10, color=c["muted"]) if len(entries) > max_items else ft.Container(),
                                 ft.IconButton(ft.Icons.ADD, icon_size=13, width=23, height=23, icon_color=c["dim"],
                                 tooltip=f"Add on {day:%d %b}", on_click=lambda e, d=day: self.edit(on=d))], spacing=0),
                         *[self.compact(item) for item in entries[:max_items]]]
                cells.append(ft.Container(ft.Column(items, spacing=3), expand=True, padding=8,
                                          bgcolor=c["surface"] if current else c["bg"], border=ft.border.all(1, c["accent"] if is_today else c["border"]),
                                          border_radius=9, ink=True, tooltip=day.strftime("%A, %d %B"), on_click=lambda e, d=day: self.open_day(d)))
            rows.append(ft.Row(cells, expand=True, spacing=5, vertical_alignment=ft.CrossAxisAlignment.STRETCH))
        return ft.Column(rows, expand=True, spacing=5)

    def weekly(self, start):
        c, columns = self.c, []
        for i in range(7):
            day = start + timedelta(days=i)
            entries, listing = self.items(day), []
            for group, items in [("ALL DAY", [e for e in entries if not e.get("time")]), ("SCHEDULED", [e for e in entries if e.get("time")])]:
                if items:
                    listing += [label(group, c, 9), *[self.compact(item, weekly=True) for item in items]]
            listing = listing or [label("No plans yet", c, 11)]
            columns.append(ft.Container(ft.Column([
                ft.Container(ft.Column([label(day.strftime("%a").upper(), c, 11),
                                         ft.Text(day.strftime("%d"), size=25, color=c["accent"] if day == date.today() else c["text"], weight=ft.FontWeight.W_600)], spacing=4),
                             ink=True, on_click=lambda e, d=day: self.open_day(d)),
                ft.Divider(height=1, color=c["border"]), ft.ListView(listing, expand=True, spacing=9),
                ft.IconButton(ft.Icons.ADD, tooltip="Add item", icon_color=c["muted"], on_click=lambda e, d=day: self.edit(on=d))], expand=True, spacing=12),
                bgcolor=c["surface"], border=ft.border.all(1, c["accent"] if day == date.today() else c["border"]), border_radius=12, padding=12, expand=True))
        return ft.Row(columns, expand=True, spacing=7, vertical_alignment=ft.CrossAxisAlignment.STRETCH)

    def detailed(self, item):
        c = self.c
        lead = ft.Checkbox(value=item.get("done", False), active_color=c["accent"], on_change=lambda e: self.complete(item, e.control.value)) if item["kind"] == "task" else ft.Icon(ft.Icons.DESCRIPTION_OUTLINED, color=c["cyan"], size=23)
        text = [ft.Text(item["title"], size=16, color=c["muted"] if item.get("done") else c["text"], weight=ft.FontWeight.W_500),
                label((item.get("time") or "All day") + (" · " + item.get("origin", "") if item.get("origin") else ""), c, 11)]
        if item.get("details"):
            text.append(ft.Text(item["details"], size=13, color=c["muted"], selectable=True))
        if item.get("note"):
            text.append(ft.TextButton("Open note: " + item["note"], icon=ft.Icons.NORTH_EAST, style=ft.ButtonStyle(color=c["cyan"]), on_click=lambda e: self.open_note(item["note"])))
        return panel(ft.Row([lead, ft.Column(text, expand=True, spacing=8),
                             ft.IconButton(ft.Icons.EDIT_OUTLINED, tooltip="Edit item", icon_size=18, icon_color=c["muted"], on_click=lambda e: self.edit(item)),
                             ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip="Delete item", icon_size=18, icon_color=c["dim"], on_click=lambda e: self.remove(item))], spacing=14), c, padding=18)

    def daily(self):
        c, entries = self.c, self.items(self.day)
        tasks, notes = [e for e in entries if e["kind"] == "task"], [e for e in entries if e["kind"] == "note"]
        parts = [ft.Row([ft.Column([ft.Text(self.day.strftime("%A"), size=32, color=c["text"], weight=ft.FontWeight.W_600),
                                    label(f"{len(tasks)} tasks · {len(notes)} notes · {sum(e.get('done', False) for e in tasks)} completed", c)], spacing=6),
                          ft.Container(expand=True), button("Add to this day", lambda e: self.edit(), c, ft.Icons.ADD)], spacing=15)]
        for name, group in [("All day", [e for e in entries if not e.get("time")]), ("Scheduled", [e for e in entries if e.get("time")])]:
            if group:
                parts += [ft.Text(name, size=14, color=c["muted"], weight=ft.FontWeight.W_600), *[self.detailed(e) for e in group]]
        if not entries:
            parts.append(panel(ft.Column([ft.Icon(ft.Icons.WB_SUNNY_OUTLINED, color=c["accent"], size=35), ft.Text("A little room in your day.", size=21, color=c["text"]),
                                           label("Add a task, write a note, or schedule something from Unscheduled.", c)], spacing=16), c, padding=30))
        return ft.ListView(parts, expand=True, spacing=14)

    def open_note(self, name):
        if name not in self.app.vault.names():
            self.app.notify("This linked note has moved or been renamed. Edit the calendar item to update its link.", True)
            return
        self.app.open_note(name)

    def complete(self, item, value):
        try:
            self.app.calendar.complete(item["id"], value)
            self.refresh(True)
        except (OSError, ValueError) as error:
            self.app.notify(str(error), True)

    def remove(self, item):
        def delete():
            self.app.calendar.delete(item["id"])
            self.refresh(True)
        self.app.confirm("Delete calendar item?", "A copy of the calendar is retained in .history.", delete)

    def unscheduled(self, _):
        items = self.app.calendar.entries(unscheduled=True)
        dialog = ft.AlertDialog(title=ft.Text("Unscheduled", color=self.c["text"]), bgcolor=self.c["surface"])
        def schedule(item):
            self.app.page.close(dialog)
            self.edit(item)
        rows = [ft.Container(ft.Row([ft.Column([ft.Text(e["title"], color=self.c["text"], size=13), label(e.get("origin") or "No date assigned", self.c, 10)], expand=True),
                                     ft.Icon(ft.Icons.CHECK, color=self.c["success"], size=16) if e.get("done") else ft.Container(),
                                     button("Schedule / edit", lambda event, item=e: schedule(item), self.c)], spacing=12), padding=10) for e in items]
        dialog.content = ft.Container(ft.Column([label("Original checklists were copied here once. Assign a date when you are ready.", self.c),
                                                 ft.ListView(rows or [label("All items have a date.", self.c)], expand=True, spacing=4)], expand=True), width=680, height=420)
        dialog.actions = [ft.TextButton("Close", on_click=lambda e: self.app.page.close(dialog))]
        self.app.page.open(dialog)

    def edit(self, item=None, on=None):
        c, item = self.c, item or {}
        title = field(c, label="Title", value=item.get("title", ""), autofocus=True, max_length=240)
        kind = ft.Dropdown(label="Type", value=item.get("kind", "task"), color=c["text"], border_color=c["border"], expand=True,
                           options=[ft.dropdown.Option("task", "Task"), ft.dropdown.Option("note", "Note")])
        day = field(c, label="Date · YYYY-MM-DD", value=item.get("date") or ("" if item else (on or self.day).isoformat()), expand=True)
        day.suffix = ft.IconButton(ft.Icons.CALENDAR_MONTH_OUTLINED, icon_color=c["muted"], icon_size=18, tooltip="Choose date", on_click=lambda e: self.pick_date(day))
        hour = field(c, label="Time · HH:MM (optional)", value=item.get("time") or "", expand=True)
        details = field(c, label="Details / day note", value=item.get("details", ""), multiline=True, min_lines=3, max_lines=5)
        names = self.app.vault.names()
        if item.get("note") and item["note"] not in names:
            names.append(item["note"])
        note = ft.Dropdown(label="Link a vault note (optional)", value=item.get("note") or "__none__", color=c["text"], border_color=c["border"],
                           width=670, label_style=ft.TextStyle(size=12, color=c["muted"]), enable_filter=True, enable_search=True,
                           options=[ft.dropdown.Option("__none__", "No linked note"), *[ft.dropdown.Option(n) for n in names]])
        done, error = ft.Checkbox(label="Task completed", value=item.get("done", False)), ft.Text(color=c["danger"], size=12)
        dialog = ft.AlertDialog(title=ft.Text("Edit calendar item" if item else "New calendar item", color=c["text"]), bgcolor=c["surface"],
                                content=ft.Container(ft.Column([title, ft.Row([kind, day, hour], spacing=10), details, note, done,
                                                                 label("Leave the date empty for Unscheduled, or the time empty for All day.", c, 11), error],
                                                                spacing=14, tight=True, scroll=ft.ScrollMode.AUTO), width=670, height=435))
        def save(_):
            try:
                if item.get("id"):
                    current = next((entry for entry in self.app.calendar.entries() if entry["id"] == item["id"]), None)
                    if current != item:
                        raise ValueError("This calendar item changed while the editor was open. Reopen it before saving.")
                self.app.calendar.save({"title": title.value, "kind": kind.value, "date": (day.value or "").strip(), "time": (hour.value or "").strip(),
                                        "details": details.value or "", "note": "" if note.value == "__none__" else note.value, "done": done.value, "origin": item.get("origin", "")}, item.get("id"))
                self.app.page.close(dialog)
                self.refresh(True)
            except (OSError, ValueError) as exc:
                error.value = str(exc)
                error.update()
        dialog.actions = [ft.TextButton("Cancel", on_click=lambda e: self.app.page.close(dialog)), ft.TextButton("Save item", on_click=save)]
        self.app.page.open(dialog)
