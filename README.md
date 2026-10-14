# Peter (McGuire) · NexusDesk

A local Fedora desktop assistant built on the original NexusDesk architecture.
Peter's language model comes from your installed LM Studio models and is selected
through its CLI registry and local API. Manage Peter's language-model weights in
LM Studio.

A central animated Peter bubble connects voice and typed conversation with your
existing Notes, Calendar, tasks, lists, memories, tools and AI Library.

## Start

Double-click **Peter.desktop** on the Desktop, choose **Peter (McGuire)** in your
application menu, or run `./start-linux.sh` from the project directory. LM Studio's
  selected model and the installed local voice APIs start automatically.

For a fresh checkout, use a compatible Python 3.11/3.12 interpreter with
`PETER_PYTHON=/path/to/python3.12 ./install-linux.sh`, then `./install-voice.sh`.
The initial setup needs internet; installed inference, transcription and speech
run locally. No Gemma weights are included or downloaded.

## Use

- The [Thinking Orbs](https://github.com/Jakubantalik/thinking-orbs) animation plays locally in the native client. Typed input, speech transcripts and Peter's replies stay visible on Home.
- **Hands-free** starts enabled: speak normally and pause for about a second to
  send. Peter ignores microphone input during his reply and listens again
  afterwards; one capture stream stays open to avoid device restarts. Speech detection and transcription run locally; ambient audio is
  not saved. Turn the toggle off for **Speak to Peter**, the bubble or
  **Ctrl+Space**, then click again to finish. **Stop** cancels the current request.
  Typed input uses the same Assistant. Peter speaks naturally in a few short
  sentences, with extended reasoning disabled and a 512-token reply budget.
  Up to 40 recent messages fit into a budgeted conversation window, retaining
  user corrections and complete turns instead of fixed short excerpts. Mail
  follow-ups use verified message references; searches resolve local dates and
  times directly, and unsupported email claims are checked before playback.
- The previous message appears above the current subtitle, smaller and translucent.
  Click it to open history at the bottom. Live captions fade after 20 idle seconds.
  Spoken replies display each sentence when its audio starts; later sentences
  generate during playback. Full replies remain available in history.
- Ask **Open my latest email**, **Open it**, or **Open report.pdf**. Email links
  use the connected Gmail account; files open in their default application.
  Filenames are matched in Notes, Desktop, Documents and Downloads; an exact
  path can identify a file elsewhere. Multiple matches require a choice. Peter
  may offer to open an email and waits for your reply before doing so.
- The bubble changes with listening, thinking and speaking. Startup greeting and
  speech mute are configurable in Settings.
- Calendar, Notes, Memory, utility tools and AI Library retain their existing
  detailed views. A cleaned Notes workspace remains empty until you create a note.
- Assistant changes use the existing validated tools, disk verification,
  transactions and undo. Sources and saved-change summaries remain in conversation.

Read [Fedora setup and limitations](LINUX_PETER.md),
[verified results](PETER_VALIDATION.md), [Assistant architecture](ASSISTANT.md),
and [Personal Memory](PERSONAL_MEMORY.md). The
[original Windows documentation](docs/LEGACY_WINDOWS.md) is preserved as history.

## Development

Flet is pinned to 0.28.3. Use the project's isolated Linux `.venv`.
Install `requirements-dev.txt`, run `python -m pytest -q`, and use
`python main_flet.py --web --port 8550` for a loopback UI preview with an isolated
`NEXUS_DATA_DIR`. Python code owns the native app; the retained TypeScript modules
are historical disconnected UI components, not a second served application.

Runtime data, notes/tasks, credentials, weights, service settings and backups are
excluded from Git and source packages. Keep local data backups separately.
