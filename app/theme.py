"""Peter's dark observatory palette and shared native workspace controls."""
from __future__ import annotations

import re
import flet as ft

DARK = dict(bg="#080B10", sidebar="#0B1017", surface="#101720", raised="#192330",
            hover="#243241", border="#25313E", text="#EDF5FA", muted="#9BAFBF",
            dim="#637D90", accent="#A5B4FF", cyan="#67E6ED", violet="#B5A1FF", success="#83D8B5", danger="#FF94AA", amber="#E8BF7C")
LIGHT = dict(bg="#F4F6FA", sidebar="#FFFFFF", surface="#FFFFFF", raised="#EBEFF6",
             hover="#E1E7F2", border="#D6DEEA", text="#202938", muted="#536174",
             dim="#657489", accent="#4859BD", cyan="#007D70", violet="#7659BA", success="#247C59", danger="#B83250", amber="#886013")


def palette(config):
    colors = dict(DARK if config.get("theme_dark", True) else LIGHT)
    accent = config.get("accent_color", "")
    if accent == "#9C8CFF":
        accent = DARK["accent"]
    if re.fullmatch(r"#[0-9a-fA-F]{6}", accent):
        colors["accent"] = accent if config.get("theme_dark", True) else (
            LIGHT["accent"] if accent == DARK["accent"] else accent)
    colors["accent_text"] = contrast_text(colors["accent"])
    return colors


def contrast_text(color):
    channels = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [channel / 12.92 if channel <= .04045 else ((channel + .055) / 1.055) ** 2.4 for channel in channels]
    luminance = sum(channel * weight for channel, weight in zip(linear, (.2126, .7152, .0722)))
    return "#10131A" if luminance > .18 else "#FFFFFF"


def label(text, c, size=12):
    return ft.Text(text, size=size, color=c["muted"], weight=ft.FontWeight.W_500)


def heading(title, subtitle, c, actions=None):
    return ft.Row([ft.Column([ft.Text(title, size=28, weight=ft.FontWeight.W_600, color=c["text"]),
                              label(subtitle, c, 13)], spacing=5, expand=True), *(actions or [])], spacing=16)


def panel(content, c, padding=20, **kwargs):
    return ft.Container(content=content, bgcolor=c["surface"], padding=padding, border_radius=16,
                        border=ft.border.all(1, c["border"]), **kwargs)


def button(text, callback, c, icon=None, primary=False, **kwargs):
    return ft.TextButton(text, icon=icon, on_click=callback,
                         style=ft.ButtonStyle(color=c["accent_text"] if primary else c["text"],
                                              bgcolor=c["accent"] if primary else c["raised"],
                                              padding=ft.padding.symmetric(horizontal=18, vertical=13),
                                              shape=ft.RoundedRectangleBorder(radius=10)), **kwargs)


def field(c, **kwargs):
    defaults = dict(color=c["text"], bgcolor=c["bg"], border_color=c["border"],
                    focused_border_color=c["accent"], border_radius=10,
                    text_size=13, label_style=ft.TextStyle(color=c["muted"], size=12),
                    hint_style=ft.TextStyle(color=c["dim"]),
                    content_padding=ft.padding.symmetric(horizontal=14, vertical=14))
    defaults.update(kwargs)
    return ft.TextField(**defaults)


def pill(text, color, c):
    return ft.Container(ft.Text(text, size=10, weight=ft.FontWeight.W_600, color=color),
                        bgcolor=c["raised"], border_radius=6,
                        padding=ft.padding.symmetric(horizontal=9, vertical=5))
