"""Hue, saturation and lightness controls with a local, unsaved live theme preview."""
import colorsys
import re

import flet as ft

from app.theme import button, label, palette


def hsl_color(hue, saturation, lightness):
    rgb = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)
    return "#" + "".join(f"{round(channel * 255):02X}" for channel in rgb)


class AppearancePicker(ft.Column):
    def __init__(self, app, accent, dark):
        super().__init__(spacing=12)
        self.app, self.accent, self.dark = app, accent, dark
        rgb = tuple(int(accent.value[i:i + 2], 16) / 255 for i in (1, 3, 5))
        hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
        self.hue = ft.Slider(min=0, max=360, value=hue * 360, divisions=360, label="{value}", on_change=self.slide)
        self.saturation = ft.Slider(min=0, max=100, value=saturation * 100, divisions=100, label="{value}%", on_change=self.slide)
        self.lightness = ft.Slider(min=15, max=85, value=max(15, min(85, lightness * 100)), divisions=70, label="{value}%", on_change=self.slide)
        self.preview = ft.Container(padding=16, border_radius=8)
        self.swatch = ft.Container(width=32, height=32, border_radius=6)
        self.hex_label = ft.Text(size=12, font_family="Consolas")
        self.controls = [
            ft.Row([label("Accent colour", app.c), self.swatch, self.hex_label]),
            ft.Row([ft.Text("Hue", width=75, color=app.c["muted"], size=12), ft.Container(self.hue, expand=True)]),
            ft.Row([ft.Text("Saturation", width=75, color=app.c["muted"], size=12), ft.Container(self.saturation, expand=True)]),
            ft.Row([ft.Text("Lightness", width=75, color=app.c["muted"], size=12), ft.Container(self.lightness, expand=True)]),
            self.preview]
        accent.on_change = self.typed
        dark.on_change = lambda e: self.refresh_preview()
        self.refresh_preview(update=False)

    def slide(self, _):
        self.accent.value = hsl_color(self.hue.value, self.saturation.value, self.lightness.value)
        self.accent.error_text = None
        self.accent.update()
        self.refresh_preview()

    def typed(self, _):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.accent.value or ""):
            return
        rgb = tuple(int(self.accent.value[i:i + 2], 16) / 255 for i in (1, 3, 5))
        hue, lightness, saturation = colorsys.rgb_to_hls(*rgb)
        self.hue.value, self.saturation.value = hue * 360, saturation * 100
        self.lightness.value = max(15, min(85, lightness * 100))
        self.refresh_preview()

    def refresh_preview(self, update=True):
        c = palette(dict(theme_dark=self.dark.value, accent_color=self.accent.value))
        self.swatch.bgcolor, self.hex_label.value, self.hex_label.color = c["accent"], self.accent.value.upper(), c["accent"]
        for slider in (self.hue, self.saturation, self.lightness):
            slider.active_color = c["accent"]
        self.preview.bgcolor, self.preview.border = c["bg"], ft.border.all(1, c["border"])
        self.preview.content = ft.Column([
            ft.Row([ft.Icon(ft.Icons.HUB_OUTLINED, size=20, color=c["accent"]),
                    ft.Text("Live preview", color=c["text"], size=15, weight=ft.FontWeight.W_600)]),
            ft.Row([ft.Text("A connected workspace", color=c["muted"], size=12, expand=True),
                    button("Selected", None, c, ft.Icons.CHECK, primary=True)]),
            ft.ProgressBar(value=.65, color=c["accent"], bgcolor=c["raised"], height=4)], spacing=12)
        if update:
            self.update()
