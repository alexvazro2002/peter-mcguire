"""Resizable wiki-link graph with retained canvas shapes and bounded drag events."""
import math
import flet as ft
import flet.canvas as cv
from app.storage import link_references


def distance_to_segment(x, y, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    ratio = max(0, min(1, ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy))) if dx or dy else 0
    return math.hypot(x - x1 - ratio * dx, y - y1 - ratio * dy)


class NoteGraph(ft.Container):
    def __init__(self, app, height=None):
        page_width = app.page.width or 1440
        width = max(330, page_width - 94 - 56 - 228 - 14 - 40)
        initial_height = height or max(260, (getattr(app.page, "height", None) or 900) - 300)
        super().__init__(height=height, expand=height is None, border_radius=12,
                         bgcolor=app.c["bg"], clip_behavior=ft.ClipBehavior.HARD_EDGE)
        self.app, self.canvas_width, self.canvas_height = app, float(width), float(initial_height)
        self.zoom, self.pan_x, self.pan_y = 1.0, 0.0, 0.0
        self.popup, self.selected_edge = None, None
        self.dragged_node = None
        self.names, self.edges = app.vault.graph()
        self.positions = {}
        for i, name in enumerate(self.names):
            old = app.cfg["notes_graph_pos"].get(name, {})
            if 0 <= old.get("x", -1) <= 1 and 0 <= old.get("y", -1) <= 1:
                self.positions[name] = [old["x"], old["y"]]
            else:
                angle = i * 2.3999632297
                radius = 0.1 + 0.30 * math.sqrt((i + 1) / max(1, len(self.names)))
                self.positions[name] = [0.5 + radius * math.cos(angle), 0.5 + radius * math.sin(angle)]
        # The dot grid stays on its own canvas and never travels over the wire during a drag.
        self.background = cv.Canvas([], expand=True)
        self.canvas = cv.Canvas([], expand=True, resize_interval=100, on_resize=self.resize)
        self.edge_shapes, self.node_shapes = {}, {}
        self.incident_edges = {name: [] for name in self.names}
        c = app.c
        for edge in self.edges:
            self.edge_shapes[edge] = cv.Line(0, 0, 0, 0, paint=ft.Paint(color=c["accent"], stroke_width=1))
            for name in edge:
                self.incident_edges[name].append(edge)
        connected = {name for edge in self.edges for name in edge}
        for name in self.names:
            selected = name == app.note_name
            color = c["accent"] if selected or name in connected else c["cyan"]
            self.node_shapes[name] = (
                cv.Circle(0, 0, 20 if selected else 11, paint=ft.Paint(color=ft.Colors.with_opacity(0.10, color))),
                cv.Circle(0, 0, 6 if selected else 4, paint=ft.Paint(color=color)),
                cv.Text(0, 0, name, style=ft.TextStyle(size=11, color=c["text"] if selected else c["muted"],
                                                    weight=ft.FontWeight.W_600 if selected else None),
                        alignment=ft.alignment.top_center, text_align=ft.TextAlign.CENTER,
                        max_lines=1, max_width=136, ellipsis="…"))
        self.canvas.shapes = [*self.edge_shapes.values(), *(shape for group in self.node_shapes.values() for shape in group)]
        self.stack = ft.Stack([self.background, self.canvas], expand=True, fit=ft.StackFit.EXPAND)
        self.content = ft.GestureDetector(content=self.stack, expand=True, drag_interval=32,
                                          on_pan_start=self.pan_start, on_pan_update=self.pan, on_pan_end=self.pan_end,
                                          on_scroll=self.scroll, on_tap_up=self.tap)
        self.draw_background()
        self.paint()

    def hit_node(self, x, y):
        hits = []
        for name in self.names:
            nx, ny = self.xy(name)
            if math.hypot(x - nx, y - ny) <= 14 or (abs(x - nx) <= 68 and 10 <= y - ny <= 30):
                hits.append((math.hypot(x - nx, y - ny), name))
        return min(hits)[1] if hits else None

    def tap(self, event):
        name = self.hit_node(event.local_x, event.local_y)
        if name:
            self.app.open_note(name)
        else:
            self.tap_edge(event)

    def edge_references(self, edge):
        rows = []
        for source, target in (edge, tuple(reversed(edge))):
            rows.extend(dict(reference, source=source, target=target)
                        for reference in link_references(self.app.vault.read(source), target))
        return rows

    def tap_edge(self, event):
        x, y = event.local_x, event.local_y
        nearby = [(distance_to_segment(x, y, *self.xy(a), *self.xy(b)), (a, b)) for a, b in self.edges]
        nearest = min(nearby, default=(float("inf"), None))
        if nearest[0] > 9:
            self.dismiss_popup()
            return
        self.selected_edge = nearest[1]
        c = self.app.c
        width = min(320, self.canvas_width - 20)
        rows = []
        references = self.edge_references(self.selected_edge)
        for reference in references:
            rows.append(ft.Column([
                ft.Text(reference["words"], size=13, color=c["accent"], weight=ft.FontWeight.W_600),
                ft.Text(reference["text"], size=12, color=c["text"], selectable=True),
                ft.Text(f"From {reference['source']}  to {reference['target']}  /  line {reference['line']}",
                        size=10, color=c["muted"])], spacing=5))
        popup_content = ft.Column([
            ft.Row([ft.Text("Linked passage", size=11, color=c["muted"], expand=True),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=15, width=28, height=28,
                                  tooltip="Close link preview", icon_color=c["muted"], on_click=self.dismiss_popup)]),
            ft.ListView(rows, expand=True, spacing=14)], spacing=6)
        description = "Linked passage. " + " ".join(f"{r['words']}. From {r['source']} to {r['target']}. {r['text']}" for r in references)
        self.popup = ft.Container(ft.Semantics(popup_content, label=description, container=True, live_region=True),
            width=width, height=190, padding=12, border_radius=8, bgcolor=c["surface"],
            border=ft.border.all(1, c["accent"]),
            left=max(10, min(x + 8, self.canvas_width - width - 10)),
            top=max(10, min(y + 8, self.canvas_height - 200)))
        self.paint(update=True)

    def dismiss_popup(self, _=None):
        self.popup, self.selected_edge = None, None
        self.paint(update=True)

    def xy(self, name):
        x, y = self.positions[name]
        return ((x - 0.5) * self.canvas_width * self.zoom + self.canvas_width / 2 + self.pan_x,
                (y - 0.5) * self.canvas_height * self.zoom + self.canvas_height / 2 + self.pan_y)

    def resize(self, event):
        if event.width <= 0 or event.height <= 0:
            return
        if (event.width, event.height) == (self.canvas_width, self.canvas_height):
            return
        self.canvas_width, self.canvas_height = event.width, event.height
        self.popup, self.selected_edge = None, None
        self.draw_background()
        if self.page:
            self.background.update()
        self.paint(update=True)

    def pan_start(self, event):
        self.dragged_node = self.hit_node(event.local_x, event.local_y)

    def pan_end(self, event):
        if self.dragged_node:
            self.save_positions(event)
        self.dragged_node = None

    def pan(self, event):
        if self.dragged_node:
            self.drag(self.dragged_node, event)
            return
        self.popup, self.selected_edge = None, None
        self.pan_x += event.delta_x
        self.pan_y += event.delta_y
        self.paint(update=True)

    def scroll(self, event):
        self.popup, self.selected_edge = None, None
        old_zoom = self.zoom
        self.zoom = max(0.45, min(2.5, old_zoom - event.scroll_delta_y / 1200))
        # Keep the point under the cursor fixed while zooming.
        x = getattr(event, "local_x", self.canvas_width / 2) - self.canvas_width / 2
        y = getattr(event, "local_y", self.canvas_height / 2) - self.canvas_height / 2
        self.pan_x = x - (x - self.pan_x) * self.zoom / old_zoom
        self.pan_y = y - (y - self.pan_y) * self.zoom / old_zoom
        self.paint(update=True)

    def reset(self, _=None):
        self.popup, self.selected_edge = None, None
        self.zoom, self.pan_x, self.pan_y = 1, 0, 0
        self.paint(update=True)

    def drag(self, name, event):
        self.popup, self.selected_edge = None, None
        self.positions[name][0] = max(0.02, min(0.98, self.positions[name][0] + event.delta_x / (self.canvas_width * self.zoom)))
        self.positions[name][1] = max(0.03, min(0.96, self.positions[name][1] + event.delta_y / (self.canvas_height * self.zoom)))
        self.paint(update=True, moved=name)

    def save_positions(self, _):
        self.app.cfg["notes_graph_pos"] = {n: dict(x=p[0], y=p[1]) for n, p in self.positions.items()}
        self.app.persist()

    def draw_background(self):
        dot_paint = ft.Paint(color=self.app.c["border"])
        self.background.shapes = [cv.Circle(x, y, 1, paint=dot_paint)
                                  for x in range(20, int(self.canvas_width), 28)
                                  for y in range(20, int(self.canvas_height), 28)]

    def paint(self, update=False, moved=None):
        c = self.app.c
        edges = self.incident_edges[moved] if moved else self.edges
        # Clear a previous highlight even when only a single node moves.
        for edge, line in self.edge_shapes.items():
            active = edge == self.selected_edge
            color, stroke = (c["cyan"], 2) if active else (c["accent"], 1)
            if line.paint.color != color or line.paint.stroke_width != stroke:
                line.paint = ft.Paint(color=color, stroke_width=stroke)
        for a, b in edges:
            x1, y1 = self.xy(a)
            x2, y2 = self.xy(b)
            line = self.edge_shapes[(a, b)]
            line.x1, line.y1, line.x2, line.y2 = x1, y1, x2, y2
        for name in [moved] if moved else self.names:
            x, y = self.xy(name)
            halo, dot, text = self.node_shapes[name]
            halo.x, halo.y = dot.x, dot.y = x, y
            text.x, text.y = x, y + 10
        controls = [self.background, self.canvas, *([self.popup] if self.popup is not None else [])]
        popup_changed = self.stack.controls != controls
        if popup_changed:
            self.stack.controls = controls
        if update and self.page:
            if popup_changed:
                self.stack.update()
            else:
                self.canvas.update()
