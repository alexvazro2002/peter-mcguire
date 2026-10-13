"""Unprivileged X11 input and hotkeys for the preserved automation utilities."""
import os


def require_x11(environ=None):
    env = os.environ if environ is None else environ
    if os.name != "nt" and (env.get("XDG_SESSION_TYPE", "").lower() == "wayland" or env.get("WAYLAND_DISPLAY")):
        raise RuntimeError("This automation needs an X11 desktop session. Fedora Wayland blocks global input injection and hotkeys. Log into an X11 session to use it; running Peter as root will not fix this.")
    if os.name != "nt" and not env.get("DISPLAY"):
        raise RuntimeError("This automation needs a running X11 desktop (DISPLAY).")


class Keyboard:
    def __init__(self):
        self.controller, self.listener, self.keys = None, None, None
        self.pressed, self.hotkeys = set(), {}

    def start(self):
        if self.controller is not None:
            return
        require_x11()
        try:
            from pynput.keyboard import Controller, Key, Listener
        except ImportError as error:
            raise RuntimeError("Install the optional requirements-automation.txt dependencies for desktop automation.") from error
        self.keys, self.controller = Key, Controller()
        def name(key):
            if getattr(key, "char", None):
                return key.char.lower()
            if key in {Key.ctrl, Key.ctrl_l, Key.ctrl_r}:
                return "ctrl"
            return str(key)
        def down(key):
            self.pressed.add(name(key))
        def up(key):
            value = name(key)
            self.pressed.discard(value)
            callback = self.hotkeys.get(value)
            if callback:
                callback()
        self.listener = Listener(on_press=down, on_release=up)
        self.listener.start()

    def add_hotkey(self, name, callback):
        self.start()
        self.hotkeys[name.casefold()] = callback

    def is_pressed(self, name):
        self.start()
        return name.casefold() in self.pressed

    def _key(self, key):
        self.start()
        return getattr(self.keys, key, key)

    def press(self, key):
        self.start()
        self.controller.press(self._key(key))

    def release(self, key):
        if self.controller is not None:
            self.controller.release(self._key(key))

    def unhook_all_hotkeys(self):
        self.hotkeys.clear()
        if self.listener:
            self.listener.stop()


keyboard = Keyboard()
