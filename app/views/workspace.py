"""Overview, tool catalog, tool forms, and settings built from one shared palette."""
import re
import secrets
from pathlib import Path
from zoneinfo import ZoneInfo
import flet as ft

from app.config import DATA_DIR
from app.storage import Vault
from app.theme import button, field, heading, label, panel, pill
from app.tools import CONVERSION, TOOLS, tool_command
from library.server import validate_url
from app.views.home import build_home
from app.views.appearance import AppearancePicker
from app.notifications import topic_url
from app.voice import local_voice_url


DOCKER_TOOLS = {"metube", "stirling_pdf"}


def run_tool(app, tool, action="launch"):
    try:
        command = tool_command(tool, app.tool_values.setdefault(tool.key, {}))
        if tool.key in DOCKER_TOOLS:
            command += ["--action", action]
        app.tool_session.start(command, graceful_input=tool.key == "afk")
        app.tool_logs = []
        app.tool_title = tool.title
        app.cfg["usage"][tool.key] = app.cfg["usage"].get(tool.key, 0) + 1
        app.persist()
        app.render()
    except (ValueError, OSError) as error:
        app.notify(str(error), True)


def tool_tile(app, tool):
    c = app.c
    icon = ft.Container(ft.Icon(tool.icon, size=19, color=c["accent"]), bgcolor=c["raised"],
                        width=36, height=36, alignment=ft.alignment.center, border_radius=9)
    return panel(ft.Row([
        icon, ft.Column([
            ft.Text(tool.title, size=14, color=c["text"], weight=ft.FontWeight.W_600,
                    max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
            ft.Text(tool.description, size=11, color=c["muted"], max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)
        ], expand=True, spacing=5),
        ft.Icon(ft.Icons.NORTH_EAST if tool.key in DOCKER_TOOLS else ft.Icons.CHEVRON_RIGHT, size=15, color=c["dim"])
    ], spacing=12), c, padding=14, height=68 if app.cfg.get("view_mode") == "list" else 94, ink=True,
        tooltip=tool.description, on_click=lambda e: app.open_tool(tool))


def build_tools(app):
    c = app.c
    catalog = ft.Column(spacing=22)
    count = label("", c, 11)
    categories = ("All", "Files", "Media", "Automation")
    category_buttons = {}
    layout_buttons = {}

    def refresh(event=None):
        if event:
            app.tool_search = event.control.value or ""
        query = app.tool_search.casefold().strip()
        tools = [t for t in TOOLS if query in (t.title + t.description + t.category).casefold()
                 and (app.tool_category == "All" or t.category == app.tool_category)]
        order = app.cfg.get("tool_order", "alphabetical")
        tools.sort(key=(lambda t: (-app.cfg["usage"].get(t.key, 0), t.title.casefold())) if order == "most_used" else lambda t: t.title.casefold(),
                   reverse=order == "alphabetical_desc")
        sections = []
        for category in categories[1:]:
            group = [t for t in tools if t.category == category]
            if not group:
                continue
            sections.append(ft.Column([
                ft.Row([ft.Text(category, size=15, color=c["text"], weight=ft.FontWeight.W_600),
                        label(str(len(group)), c, 11), ft.Container(ft.Divider(color=c["border"]), expand=True)], spacing=10),
                ft.ResponsiveRow([ft.Container(tool_tile(app, tool),
                                               col=12 if app.cfg.get("view_mode") == "list" else {"xs": 12, "md": 6, "xl": 4})
                                  for tool in group], spacing=10, run_spacing=10)
            ], spacing=10))
        catalog.controls = sections or [panel(label("No tools match. Try another search or category.", c), c)]
        count.value = f"{len(tools)} of {len(TOOLS)} tools"
        for category, control in category_buttons.items():
            control.style = ft.ButtonStyle(color=c["accent"] if app.tool_category == category else c["muted"],
                                           bgcolor=c["raised"] if app.tool_category == category else None,
                                           shape=ft.RoundedRectangleBorder(radius=8))
        for mode, control in layout_buttons.items():
            control.icon_color = c["accent"] if app.cfg.get("view_mode", "grid") == mode else c["muted"]
            control.bgcolor = c["raised"] if app.cfg.get("view_mode", "grid") == mode else None
        if event:
            content.update()

    def order(event):
        app.cfg["tool_order"] = event.control.value
        app.persist()
        refresh()
        content.update()

    def category(selected):
        app.tool_category = selected
        refresh()
        content.update()

    def layout(mode):
        app.cfg["view_mode"] = mode
        app.persist()
        refresh()
        content.update()

    for name in categories:
        category_buttons[name] = ft.TextButton(name, on_click=lambda e, selected=name: category(selected))
    for mode, icon in [("grid", ft.Icons.GRID_VIEW_ROUNDED), ("list", ft.Icons.VIEW_LIST_ROUNDED)]:
        layout_buttons[mode] = ft.IconButton(icon, icon_size=18, width=36, height=36,
                                            tooltip=f"{mode.title()} layout", on_click=lambda e, selected=mode: layout(selected))
    refresh()
    content = ft.Column([heading("Tools", "Find a utility for your files, media, or daily workflow.", c),
                      ft.Row([field(c, value=app.tool_search, hint_text="Search tools…", prefix_icon=ft.Icons.SEARCH,
                                    on_change=refresh, expand=True, dense=True,
                                    content_padding=ft.padding.symmetric(horizontal=12, vertical=10)),
                              ft.Dropdown(value=app.cfg.get("tool_order", "alphabetical"), color=c["text"], border_color=c["border"],
                                          width=150, text_size=12, dense=True, content_padding=10,
                                          options=[ft.dropdown.Option(k, v) for k, v in [("alphabetical", "A–Z"), ("alphabetical_desc", "Z–A"),
                                                                                       ("most_used", "Most used")]], on_change=order),
                              ft.Row(list(layout_buttons.values()), spacing=2)], spacing=10),
                      ft.Row([*category_buttons.values(), ft.Container(expand=True), count], spacing=4),
                      ft.Column([catalog], scroll=ft.ScrollMode.AUTO, expand=True)], expand=True, spacing=14)
    return content


def build_tool(app):
    tool, c = app.active_tool, app.c
    values = app.tool_values.setdefault(tool.key, {})
    controls = []
    for key in tool.fields:
        if key == "to":
            options = CONVERSION.get(Path(values.get("file", "")).suffix.lower(), [])
            if values.get(key) not in options:
                values[key] = ""
            controls.append(ft.Row([ft.Dropdown(label="Convert to", value=values[key] or None, color=c["text"], border_color=c["border"], expand=True,
                                        text_size=13, dense=True, content_padding=ft.padding.symmetric(horizontal=14, vertical=12),
                                        options=[ft.dropdown.Option(v, "Compress PDF" if v == "compress" else v.upper()) for v in options],
                                        on_change=lambda e: values.__setitem__("to", e.control.value))]))
        else:
            titles = {"source": "Source directory", "file": "Input file", "destination": "Playlist directory (optional)", "output": "Output directory"}
            text = field(c, label=titles[key], value=values.get(key, ""), expand=True, dense=True)
            def change(event, k=key):
                values[k] = event.control.value or ""
                if k == "file":
                    # Rebuild on blur rather than every keystroke to preserve typing focus.
                    values["to"] = ""
            text.on_change = change
            if key == "file":
                text.on_blur = lambda e: app.render()
            def picked(path, k=key):
                values[k] = path
                app.render()
            controls.append(ft.Row([text, button("Browse", lambda e, tf=text, k=key: app.pick(tf, directory=k != "file", callback=lambda p, key=k: picked(p, key)),
                                                 c, ft.Icons.FOLDER_OPEN_OUTLINED)], spacing=12))
    if tool.key == "duplicates":
        controls.append(ft.Checkbox(label="Quarantine MP3 candidates that match FLACs (recoverable)", value=bool(values.get("cleanup")),
                                    on_change=lambda e: values.__setitem__("cleanup", e.control.value)))
    if tool.warning:
        controls.append(label(tool.warning, c))
    def run(_):
        run_tool(app, tool)
    docker = tool.key in DOCKER_TOOLS
    if docker:
        controls.append(label("Clicking this brick launches the local service. First launch downloads its container image; later launches reuse it.", c))
        controls.append(label("MeTube downloads: Downloads/MeTube" if tool.key == "metube" else
                              "Stirling-PDF settings: services/stirling_pdf • Full source: projects/Stirling-PDF", c))
    actions = [button("Launch & open browser" if docker else "Run tool", run, c, ft.Icons.PLAY_ARROW_ROUNDED,
                      primary=True, disabled=app.tool_session.running),
               button("Cancel launch" if docker else "Stop", lambda e: app.stop_tool(), c, ft.Icons.STOP_ROUNDED,
                      disabled=not app.tool_session.running)]
    if docker:
        actions += [button("Stop service", lambda e: run_tool(app, tool, "stop"), c, ft.Icons.POWER_SETTINGS_NEW,
                           disabled=app.tool_session.running)]
    console = field(c, value="\n".join(app.tool_logs[-600:]), read_only=True, multiline=True, min_lines=8,
                    max_lines=12, text_style=ft.TextStyle(font_family="DejaVu Sans Mono"), text_size=11)
    app.console_control = console
    return ft.Column([
        heading(tool.title, tool.description, c, [button("All tools", lambda e: app.navigate("tools"), c, ft.Icons.ARROW_BACK)]),
        ft.ResponsiveRow([
            ft.Container(panel(ft.Column([label("SETUP", c, 10), *controls,
                                           ft.Row(actions, spacing=8, wrap=True)], spacing=12,
                                          horizontal_alignment=ft.CrossAxisAlignment.STRETCH), c, padding=16),
                         col={"xs": 12, "lg": 5}),
            ft.Container(panel(ft.Column([
                ft.Row([label("PROCESS OUTPUT", c, 10), ft.Container(expand=True),
                        pill("Running" if app.tool_session.running else "Idle", c["cyan"], c)]), console,
                ft.TextButton("Export log", icon=ft.Icons.SAVE_ALT, on_click=lambda e: app.export_log(),
                              style=ft.ButtonStyle(color=c["muted"]))], spacing=12), c, padding=16),
                         col={"xs": 12, "lg": 7})
        ], spacing=14, run_spacing=14)], expand=True, spacing=16, scroll=ft.ScrollMode.AUTO)


def build_settings(app):
    c = app.c
    inputs = {}
    library = app.cfg["library"]
    def path_input(key, title, directory=False):
        inputs[key] = field(c, label=title, value=str(library.get(key, "")), expand=True)
        return ft.Row([inputs[key], button("Browse", lambda e: app.pick(inputs[key], directory=directory), c, ft.Icons.FOLDER_OPEN_OUTLINED)], spacing=10)
    accent = field(c, label="Accent color (#RRGGBB)", value=app.cfg["accent_color"], width=250)
    dark = ft.Switch(label="Dark appearance", value=app.cfg["theme_dark"])
    appearance = AppearancePicker(app, accent, dark)
    view = ft.Dropdown(label="Tool layout", value=app.cfg.get("view_mode", "grid"), width=200, color=c["text"], border_color=c["border"],
                       options=[ft.dropdown.Option("grid", "Grid"), ft.dropdown.Option("list", "List")])
    vault = field(c, label="Notes vault (empty uses local workspace)", value=app.cfg.get("vault_path", ""), expand=True)
    source = path_input("default_source", "Default AI Library source", True)
    model = path_input("model_path", "Existing chat GGUF model (Qwen or Gemma)")
    executable = path_input("server_executable", "llama-server executable (empty auto-detects)")
    python = path_input("ai_python", "Existing AI Python interpreter (empty uses app environment)")
    provider = ft.Dropdown(label="Local model runtime", value=library.get("runtime_provider", "lmstudio"),
                           color=c["text"], border_color=c["border"],
                           options=[ft.dropdown.Option("lmstudio", "LM Studio"), ft.dropdown.Option("llama", "llama-server")])
    lmstudio_cli = path_input("lmstudio_cli", "LM Studio CLI (empty auto-detects lms)")
    inputs["embedding_model"] = field(c, label="Existing embedding model name or directory", value=library["embedding_model"])
    inputs["server_url"] = field(c, label="Local server base URL", value=library["server_url"])
    inputs["gpu_layers"] = field(c, label="GPU layers (0 = CPU, -1 = all)", value=str(library["gpu_layers"]))
    inputs["context_size"] = field(c, label="Model context size", value=str(library["context_size"]))
    inputs["max_tokens"] = field(c, label="Answer token budget", value=str(library["max_tokens"]))
    offline = ft.Switch(label="Offline models — use existing embedding cache only", value=library.get("offline_models", True))
    thinking = ft.Switch(label="Extended model reasoning (slower)", value=library.get("enable_thinking", False))
    timezone = field(c, label="Peter timezone (IANA name)", value=app.cfg["assistant_timezone"])
    peter = app.cfg.get("peter", {})
    voice_enabled = ft.Switch(label="Enable microphone and voice services", value=peter.get("enabled", True))
    autostart = ft.Switch(label="Load local model and voice at startup", value=peter.get("autostart", True))
    speech_enabled = ft.Switch(label="Speak Peter's answers aloud", value=peter.get("speech_enabled", True))
    startup_greeting = ft.Switch(label="Greet me at startup", value=peter.get("startup_greeting", True))
    greeting = field(c, label="Startup greeting", value=peter.get("greeting", "Hello. I'm Peter McGuire."), max_length=2000)
    voice_url = field(c, label="Local VoiceStudio URL", value=peter.get("voice_url", "http://127.0.0.1:3900"))
    voice_model = field(c, label="Speech model", value=peter.get("voice_model", "kittentts"), expand=True)
    voice_id = field(c, label="Voice ID", value=peter.get("voice_id", "expr-voice-2-m"), expand=True)
    asr_model = field(c, label="Transcription model", value=peter.get("asr_model", "whisper-1"), expand=True)
    record_seconds = field(c, label="Maximum recording seconds (1–60)", value=str(peter.get("record_seconds", 30)))
    notifications = app.cfg["notifications"]
    ntfy_enabled = ft.Switch(label="Send AI answer notifications with ntfy", value=notifications["enabled"])
    ntfy_server = field(c, label="ntfy server URL", value=notifications["server_url"])
    ntfy_topic = field(c, label="Private topic", value=notifications["topic"] or "nexus-" + secrets.token_hex(12), expand=True)
    include_answer = ft.Switch(label="Include answer preview (sends text to ntfy server)", value=notifications["include_answer"])
    def notification_settings():
        return dict(enabled=ntfy_enabled.value, server_url=(ntfy_server.value or "").strip(),
                    topic=(ntfy_topic.value or "").strip(), include_answer=include_answer.value)
    def subscribe(_):
        try:
            url = topic_url(notification_settings())
            app.page.launch_url(url)
        except ValueError as error:
            app.notify(str(error), True)
    def test_notification(_):
        try:
            candidate = notification_settings()
            topic_url(candidate)
            app.notifier.send(dict(candidate, enabled=True), "Test notification", "Your ntfy integration is working.")
            app.notify("Test queued. Subscribe to this topic and enable browser/desktop notifications in ntfy.")
        except ValueError as error:
            app.notify(str(error), True)
    def save(_):
        try:
            if app.assistant.busy or app.library_session.running:
                raise ValueError("Finish Peter's requests and stop AI Library before changing settings.")
            ZoneInfo((timezone.value or "").strip())
            if not re.fullmatch(r"#[0-9a-fA-F]{6}", accent.value or ""):
                raise ValueError("Accent color must be #RRGGBB.")
            candidate = {k: tf.value.strip().strip('"') for k, tf in inputs.items()}
            ntfy = notification_settings()
            if ntfy["enabled"]:
                topic_url(ntfy)
            validate_url(candidate["server_url"])
            voice_endpoint = local_voice_url((voice_url.value or "").strip())
            recording_limit = float(record_seconds.value or "0")
            if not 1 <= recording_limit <= 60:
                raise ValueError("Maximum recording duration must be between 1 and 60 seconds.")
            voice_values = dict(enabled=voice_enabled.value, autostart=autostart.value,
                                speech_enabled=speech_enabled.value, startup_greeting=startup_greeting.value,
                                greeting=greeting.value or "", voice_url=voice_endpoint,
                                voice_model=(voice_model.value or "").strip(), voice_id=(voice_id.value or "").strip(),
                                asr_model=(asr_model.value or "").strip(), record_seconds=recording_limit)
            if not all(voice_values[key] for key in ("voice_model", "voice_id", "asr_model")):
                raise ValueError("Choose a speech model, voice ID and transcription model.")
            for key, minimum, maximum in [("gpu_layers", -1, 999), ("context_size", 4096, 131072), ("max_tokens", 128, 16000)]:
                candidate[key] = int(candidate[key])
                if not minimum <= candidate[key] <= maximum:
                    raise ValueError(f"{key} must be between {minimum} and {maximum}.")
            vault_path = (vault.value or "").strip().strip('"')
            if vault_path and not Path(vault_path).expanduser().is_dir():
                raise ValueError("Choose an existing vault directory.")
            path_changed = candidate["model_path"] != library["model_path"]
            app.cfg["library"].update(candidate, offline_models=offline.value, enable_thinking=thinking.value,
                                      runtime_provider=provider.value)
            if path_changed:
                app.cfg["library"].update(model_id="", external_model=False, lmstudio_model_key="", api_model="")
            app.cfg.update(theme_dark=dark.value, accent_color=accent.value, view_mode=view.value, vault_path=vault_path,
                           assistant_timezone=timezone.value.strip(), notifications=ntfy)
            app.cfg.setdefault("peter", {}).update(voice_values)
            app.persist(raise_error=True)
            app.vault = Vault(Path(vault_path).expanduser() if vault_path else DATA_DIR / "notes")
            app.ensure_note()
            app.reset_assistant()
            app.reset_voice()
            app.assistant_model_selected = False
            app.render()
            app.notify("Settings saved. AI settings apply to the next launch.")
        except (ValueError, OSError, KeyError) as error:
            app.notify(str(error), True)
    return ft.Column([
        heading("Settings", "Make the workspace feel like yours.", c),
        panel(ft.Column([ft.Text("Appearance", size=18, color=c["text"]), ft.Row([dark, view, accent], spacing=20, wrap=True), appearance], spacing=16), c),
        panel(ft.Column([ft.Text("Notes vault", size=18, color=c["text"]),
                         ft.Row([vault, button("Browse", lambda e: app.pick(vault, directory=True), c)], spacing=10),
                         label("Markdown stays directly editable in Obsidian or another editor. Changing vaults does not move files.", c)], spacing=14), c),
        panel(ft.Column([ft.Text("Peter · Local voice", size=18, color=c["text"]), voice_enabled, autostart,
                         speech_enabled, startup_greeting, greeting, voice_url,
                         ft.Row([voice_model, voice_id, asr_model], spacing=12), record_seconds,
                         label("Tap Peter's bubble to start recording and again to finish. Speech and transcription use your local VoiceStudio APIs. Muting speech keeps microphone input available.", c, 11)], spacing=14), c),
        panel(ft.Column([ft.Text("Local AI · Library & Peter", size=18, color=c["text"]), provider, lmstudio_cli, source, model, executable, python,
                         inputs["embedding_model"], inputs["server_url"], offline,
                         ft.Row([ft.Container(inputs[k], expand=True) for k in ("gpu_layers", "context_size", "max_tokens")], spacing=12),
                         thinking, label("Quick replies: 8,192 context, 384 answer tokens, and extended reasoning off. Peter keeps older messages on screen while fitting recent context to the model.", c, 11),
                         timezone, label("Peter and AI Library share one local model. Startup loads the selected weights; the model chooser remains available. Vision projectors are optional for text chat.", c)], spacing=16), c),
        panel(ft.Column([ft.Text("AI answer notifications", size=18, color=c["text"]), ntfy_enabled, ntfy_server,
                         ntfy_topic, include_answer,
                         ft.Row([button("Subscribe in ntfy", subscribe, c, ft.Icons.NOTIFICATIONS_OUTLINED),
                                 button("Send test", test_notification, c, ft.Icons.SEND_OUTLINED)], wrap=True),
                         label("For desktop pop-ups, subscribe to this topic in ntfy and allow notifications. Use a private topic or authenticated self-hosted server. Answer previews are off by default; NEXUS_NTFY_TOKEN supplies optional authentication.", c, 11)], spacing=14), c),
        button("Save settings", save, c, ft.Icons.CHECK_ROUNDED, primary=True),
        label("Peter McGuire · NexusDesk · Local calendar and knowledge workspace", c, 11)],
        expand=True, spacing=20, scroll=ft.ScrollMode.AUTO)
