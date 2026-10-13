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
existing Gemma model and the installed local voice APIs start automatically.

For a fresh checkout, use a compatible Python 3.11/3.12 interpreter with
`PETER_PYTHON=/path/to/python3.12 ./install-linux.sh`, then `./install-voice.sh`.
The initial setup needs internet; installed inference, transcription and speech
run locally. No Gemma weights are included or downloaded.

## Use

- The [Thinking Orbs](https://github.com/Jakubantalik/thinking-orbs) animation plays locally in the native client. Typed input, speech transcripts and Peter's replies stay visible on Home.
- Click the bubble, **Speak to Peter**, or **Ctrl+Space** to capture speech. Click
  again to finish; **Stop** cancels. Typed input uses the same Assistant. Peter gives one or two short sentences by default, with extended reasoning disabled and a 384-token reply budget. Older saved messages stay available while the model receives bounded recent context.
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
