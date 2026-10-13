"""Thinking Orbs animations rendered by the native client, without frame polling."""
from __future__ import annotations

import flet as ft

STATES = {"idle", "listening", "thinking", "speaking", "error"}
ORB_STATES = {"idle": "breathing", "listening": "listening", "thinking": "working",
              "speaking": "composing", "error": "error"}


class PeterOrb(ft.Container):
    """Pinned upstream canvas animations, decoded and played locally by Flutter."""

    def __init__(self, app, size=208):
        super().__init__(width=size, height=size, alignment=ft.alignment.center,
                         on_click=lambda _: app.toggle_peter_listen(),
                         tooltip="Speak to Peter · Ctrl+Space")
        self.app, self.size, self.state = app, size, "idle"
        self.image = ft.Image(width=size, height=size, fit=ft.ImageFit.CONTAIN,
                              gapless_playback=True, filter_quality=ft.FilterQuality.MEDIUM,
                              semantics_label="Peter is ready. Click to speak.")
        self.content = self.image
        self.set_state("idle")

    def set_state(self, state):
        self.state = state if state in STATES else "idle"
        theme = "dark" if self.app.cfg.get("theme_dark", True) else "light"
        self.image.src = f"thinking-orbs/{theme}-{ORB_STATES[self.state]}.webp"
        self.image.semantics_label = {
            "idle": "Peter is ready. Click to speak.", "listening": "Peter is listening. Click to finish.",
            "thinking": "Peter is thinking.", "speaking": "Peter is speaking.",
            "error": "Peter needs attention."
        }[self.state]
