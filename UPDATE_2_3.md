# Nexus Desk 2.3

## Task persistence and truthful completion

The reported bug had a reproducible acceptance path: the model could return
"I've created your task" without calling a write tool, and the app displayed it.
The engine now rejects unsupported edit claims and retries with explicit tool
instructions, up to a bounded limit. It does not parse a particular packing/movie
example into hard-coded actions. General intent interpretation remains the model's
job, using the existing controlled tools.

Successful modifications display the application's committed change summaries,
not the model's unsupported narrative. Duplicate/no-op writes are labelled as no
new changes. Actual storage is read back before the operation is marked committed;
a mismatch triggers rollback. Clarifications discard staging. Whole-operation undo
is preserved. This guards common false-success responses, not every possible
semantic mistake by a small model; inspect the saved changes and use Undo as needed.

The regression example, anchored to Thursday **1 October 2026**, resolves Saturday
to **3 October 2026**. The tool saves `Pack for trip`, `16:00`, and details containing
hot water bottle, boots and charging electronics in the same `calendar.json` used
by Monthly, Weekly and Daily. The committed response contains the date, time and
details, with a source button opening that day. The test reloads this file and
checks the calendar view sees it, then checks whole-operation undo.

Your original failed request is not silently replayed, and no example tasks are
bundled in this update. After upgrading, resend it and verify its change summary.

## Notes and navigation

- The Notes navigation button opens Graph. Clicking a particular note still opens
  its readable content.
- Click a connection line to open an anchored, bounded preview of its real linked
  words and source line, with From/To note names and a source line number. Multiple
  links and both directions are shown when applicable. Code-block links are ignored.
- Custom order uses Flet's reorderable list and visible drag handles. Filtered
  reordering preserves hidden notes' slots. Other sort modes are plain lists with
  no dragging; their Move up/down menu commands are disabled. Pins stay prioritised
  in non-custom sorts; Custom order follows the user's exact order.
- Home is removed from the rail. The top-left N logo is now a labelled Home control.
- Appearance adds hue, saturation and lightness sliders plus a swatch, hex value
  and live sample. Preview changes remain unsaved until Save settings. Primary
  button/icon foregrounds adapt to the chosen accent's brightness.

## Model chooser and speed

The first Assistant launch in an app session opens a model chooser. Its model icon
can reopen it at any time. AI Library's Launch/Start also opens the chooser. The
same selected model/runtime is shared between both sections. Stop Library and
finish any Assistant request before switching weights. Conversation and app data
are preserved when switching. An external server is never killed by the app.

The chooser discovers the exact supplied GGUF names under `ai_models` beside the
app, under `projects/ai_models`, sibling project directories, the user's Desktop
NexusDesk directory, LM Studio models, and the configured model's folder. Copying
large weights is unnecessary. A custom existing GGUF configured in Settings is
also available. Missing paths are displayed explicitly; no downloads occur.

| Choice | Use |
| --- | --- |
| Qwen3.5-4B Q4_K_S | Recommended starting point for Assistant actions and text Library Q&A, retaining the previously used model. |
| Gemma 4 E4B Q4_K_M | Alternate general instruction/chat/code model. Compare on the same requests and hardware; this update does not claim it is faster or more accurate. |
| PaddleOCR-VL 1.6 | Document-image OCR and layout extraction. Listed for guidance but unavailable for general text chat; this app's chats do not yet accept images. |
| Current local server / LM Studio | Reuse whichever model the user already loaded. This mode does not pretend to switch an external server's weights. |

The Gemma and Paddle mmproj files are vision projectors, not alternate chat models.
Text-only chat does not load them or allocate their extra memory. An explicitly
selected GGUF cannot silently fall back to another missing/different model. If the
server advertises another model, the app explains how to load the intended one or
choose the explicit current-server option. Nonstandard aliases may require that
option. The installed llama.cpp/LM Studio backend must support the chosen weights.

**Suggested first speed change:** keep context **16,384** and answer budget **1,000**,
but turn off **Extended model reasoning** for routine work. The transport requests
`chat_template_kwargs.enable_thinking=false`, avoiding lengthy hidden reasoning
on supporting servers. Enable it again for complex reasoning requests. Older
servers that reject this option fall back safely, but may not get the speedup.
Existing context/output values are preserved on upgrade. No latency measurement
or quality guarantee can be made without your models and hardware. GPU offload may
help further, but only use a compatible backend and a layer count that fits memory.

Primary references: [Qwen model card](https://huggingface.co/Qwen/Qwen3.5-4B),
[Gemma model card](https://huggingface.co/google/gemma-4-E4B-it),
[PaddleOCR model card](https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6), and
[llama.cpp server documentation](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

## Repository questions

New read-only tools: `list_repository`, `search_repository`, `read_repository_file`.
They cover project source, utilities, scripts, tests and documentation, not just
`app/`. Search returns ranked source lines and filenames; reading is paginated.
Answers cite clickable file sources that open a read-only source viewer.

For "What tool can I use to change a PDF to a text file?", the Assistant can find
`app/tools.py` and `tools/file_converter.py` and explain **Tools > File Converter**,
PDF input and TXT output. It cannot launch commands or edit source through these
tools. Notes and tasks still use their original workspace APIs.

"All repo" means searchable/readable text throughout the project's directories,
not loading every file into one prompt. Dependencies, model binaries, caches,
history/trash, secrets, private runtime config and symlinks are excluded. Other
binary files can be listed but not read. The root cannot be escaped with absolute
paths, traversal or symlinks. Retrieved source is untrusted evidence, not authority
to make changes. This is not a complete prompt-injection defence.

## ntfy notifications

The [binwiederhier/ntfy](https://github.com/binwiederhier/ntfy) integration uses its
[official HTTP publishing API](https://docs.ntfy.sh/publish/). No fork, server
binary or extra Python package is bundled. Use `https://ntfy.sh` or an existing
self-hosted HTTPS server; local HTTP is permitted for localhost only.

1. Open Settings > AI answer notifications. Enable ntfy, choose the server and keep
   the generated hard-to-guess topic or enter your own private topic.
2. Click Subscribe in ntfy, subscribe to that topic, and allow browser notifications.
   Install the ntfy web app or enable its background notifications as appropriate.
3. Click Send test, then Save settings. Assistant and Library answers trigger a
   notification even if another Nexus Desk section is open.

The default notification only says an answer is ready. **Include answer preview**
explicitly permits sending up to 800 characters of answer text to the configured
server. Public ntfy topics are not access-controlled merely because they have a
random name; use a reserved/authenticated topic or private self-hosted server for
sensitive use. Optional bearer authentication comes from `NEXUS_NTFY_TOKEN` in
the environment, never from model context or a bundled config file. Redirects
are not followed. Failures are reported without changing the saved answer.

Publishing happens in a bounded background queue, not on the UI thread. Successful
answers also show an in-app ready notification. Desktop pop-ups require a subscribed
ntfy client and OS/browser permission; Nexus Desk cannot grant those permissions.
On Windows, Chrome/Edge generally need to remain running for web push delivery.
See [ntfy web notification support](https://docs.ntfy.sh/subscribe/web/).

## Upgrade and changed components

Extract into a new folder and keep the previous installation until verified. Back
up and migrate your `config.json`, `calendar.json`, notes/tasks, vault `.history`
and `.trash`, and `assistant-conversation.json`. Alternatively continue using your
existing `NEXUS_DATA_DIR`. Do not overwrite personal data with bundled templates.
No model weights or private generated calendar/config/chat are in the ZIP.

New modules are `app/assistant/repository.py`, `app/notifications.py`,
`app/views/appearance.py`, `app/views/model_picker.py`, `library/models.py`, and
`tests/test_updates.py`. Modified areas are Assistant engine/tools/transactions,
Notes/graph/storage, shared palette, shell/navigation, settings, model server,
chat transport, Assistant/Library views, and accent foregrounds in Home/calendar.
Unrelated utilities and their behaviour are retained.

See VALIDATION.md for verified tests, browser checks and remaining Windows/model
and real notification delivery checks.
