# Peter (McGuire) — Fedora setup and operation

Peter is NexusDesk's existing local Assistant with a new native Flet interface and
VoiceStudio audio transport. Notes, lists, calendar, tasks, personal memories,
transactions, undo, tools and the AI Library continue to use their existing stores
and modules. No second reasoning agent, external AI service or embedded VoiceStudio
interface is introduced.

## Launch

Use **Peter (McGuire)** in your application menu or **Peter.desktop** on the Desktop.
From the project directory, `./start-linux.sh` does the same thing. The launcher
starts the installed VoiceStudio backend when needed and opens NexusDesk. The app
discovers the installed LM Studio CLI, loads the existing selected local Gemma
model if needed, starts its loopback API and shares that runtime with AI Library.
If the model is already loaded, Peter reuses its exact API identifier and context.

The central bubble uses locally played Thinking Orbs animations for listening, thinking and speech; Python sends state changes, not animation frames. Click the
bubble, **Speak to Peter**, or press **Ctrl+Space** while Peter is focused. Click
again to finish capture; recordings have a configurable time limit. Peter sends the
transcript through the same Assistant used by typed input, saves actual tool
changes, and speaks the saved answer. **Stop** cancels input/output and the current
Assistant request. Typed messages, recognized speech and Peter's replies remain written in a scrollable Home transcript. Open conversation for the full saved conversation, sources, change summaries and undo.

Settings expose speech mute, startup greeting, microphone controls, recording
limit and local voice endpoint/model/voice. Muting output keeps transcription
available. Microphone capture is user activated; there is no always-on wake word
or continuous background recording.

## Fresh installation

Use Python 3.11 or 3.12 for the pinned Flet 0.28.3 client. Fedora's default Python
3.14 is not used for this project. The installer discovers Python 3.11/3.12 on PATH or an existing uv-managed installation. You can also select one explicitly:

```bash
PETER_PYTHON=/path/to/python3.12 ./install-linux.sh
./install-voice.sh
./start-linux.sh
```

`install-linux.sh` creates a project-local `.venv` (preserving an incompatible previous environment), installs the original portable
UI/tool dependencies, and creates the application/menu shortcut. Its private Linux
library setup downloads missing Fedora RPMs without installing system packages or
using sudo. Flet 0.28 asks for `libmpv.so.1`; current Fedora supplies `.so.2`. The
documented Flet compatibility alias is confined to `.runtime/lib`, used only by
the launcher. No system library links are changed.

`install-voice.sh` verifies and installs the official VoiceStudio Electron AppImage
and pins its separate backend source to version **0.5.6**, commit
`3915a62cb482bb43117aaebab57d22745bd1cc21`. It uses the release's bundled uv and an
isolated Python 3.11 runtime. It prepares KittenTTS and Whisper base through
VoiceStudio's own model-install API and verifies their installed flags.

The CPU setup uses PyTorch/torchaudio 2.8.0+cpu and torchvision 0.23.0+cpu instead
of the upstream lock's CUDA wheels. CTranslate2 4.6.0 replaces locked 4.4.0 because
the older library requests an executable stack rejected by Fedora. This is an
explicit compatibility override, tested with actual transcription; the upstream
lockfile is retained. CPU is intentional on this machine's Nouveau driver.
CUDA acceleration requires a working proprietary driver/runtime and separate
verification; Peter does not install or alter GPU drivers.

The upstream desktop AppImage remains independently launchable as `VoiceStudio`.
Peter talks directly to its backend, without loading that UI. Official references:
[VoiceStudio Linux guide](https://github.com/debpalash/VoiceStudio/blob/v0.5.6/docs/install/linux.md),
[Electron setup](https://github.com/debpalash/VoiceStudio/blob/v0.5.6/electron/README.md),
[speech API](https://github.com/debpalash/VoiceStudio/blob/v0.5.6/backend/api/routers/openai_compat.py),
and [Flet Linux media dependency](https://flet.dev/docs/controls/video/).

## Existing models and authentication

No Gemma weights are downloaded or copied. File discovery checks the actual local
LM Studio model directory; CLI discovery uses `lms ls --json` and `lms ps --json`
to resolve the installed `google/gemma-4-e4b` model and its loaded alias. Local
Gemma GGUF weights were found under `.lmstudio/models/lmstudio-community/`.
The LM Studio CLI registry is authoritative for loading the installed model.
This machine uses its verified Vulkan GPU backend with full offload (`GPU layers
= -1`). A cold CPU load failed during testing; Vulkan cold startup succeeded.
Extended reasoning is disabled using both the template flag and LM Studio's
verified `reasoning_effort="none"` parameter. This GPU choice applies to Gemma;
VoiceStudio remains on its separately verified CPU runtime.

The loaded model on this machine has an 8,192-token context. Peter uses that
context with concise replies and a 384-token ordinary answer budget. The initial
prompt carries a compact capability catalogue; exact argument schemas are fetched
only when needed. Recent conversation excerpts are bounded, and oldest complete
turns are evicted from model input when necessary. The written conversation is
retained separately. Read results use small honest pages; unusually large single
requests/tool workflows can still exceed context and should be split. The app reports an error
without committing staged changes if the request cannot fit.

Local model authentication comes from `NEXUS_MODEL_TOKEN` or
`NEXUS_MODEL_TOKEN_FILE`. This installation reuses the pre-existing authorized
`.config/lmstudio-agent/api-token` file when present. Tokens are never stored in
NexusDesk config, prompts, source control or reports. Peter does not disable LM
Studio authentication. Model and voice endpoints accept loopback addresses only.

VoiceStudio uses `http://127.0.0.1:3900`, direct `POST /v1/audio/speech` and
`POST /v1/audio/transcriptions`. KittenTTS mini 0.8 has an Apache-2.0 model license
and eight preset voices; Whisper base has an MIT model license. Their approximate
downloads are 80 MB and 150 MB. No voice is cloned. Generation and transcription
stay local after setup. Cloud integrations and analytics remain disabled unless
enabled independently in VoiceStudio.

## Data and process ownership

- `notes/`: ordinary Markdown; a new/cleaned workspace stays empty until a note is created.
- `calendar.json`, `tasks/`: existing calendar and task data.
- `personal-memory.json`: the existing memory store shared by the native dashboard and Assistant.
- `assistant-conversation.json`: Peter's local conversation.
- `notes/.history/assistant/`: the existing transaction journal and recoverable undo.
- `.runtime/VoiceStudio/`: excluded backend source and Python environment.
- `.runtime/voice-data/`, `.runtime/voice-cache/`: local speech settings, outputs and downloaded weights.
- `logs/startup.log`, `.runtime/voicestudio.log`: local diagnostics.

Closing Peter stops a voice service that its launcher started. A previously
running voice service is reused and left running. LM Studio remains independent;
existing models, sessions and its API are not killed when NexusDesk closes.
Temporary microphone/playback WAVs are removed after completion or cancellation.
VoiceStudio can retain generated audio in its own excluded local output store.

The requested note cleanup permanently removed the original vault contents,
including metadata, note history, transaction recovery copies and trash. Prior
Assistant chat was cleared because it may quote those deleted notes. Stale note
ordering/pins/graph positions and calendar note links were reset. Calendar tasks,
the weekly task file, memories, source and project documentation were preserved.
The reusable cleanup command first supports a dry run:

```bash
.venv/bin/python scripts/clean_notes.py
# Permanent deletion only when expressly wanted:
.venv/bin/python scripts/clean_notes.py --apply
```

## Existing utilities and limitations

File/media utilities retain their original worker processes and output controls.
MeTube and Stirling-PDF can use rootless Podman on Fedora with local-only ports and
SELinux bind mount labels. They download their upstream images only when requested.
The retained Stirling-PDF source snapshot stays local under `projects/`, outside
the GitHub publication. Starting its prebuilt image does not build that snapshot.

Mews Segments and Activity Script need an **X11 desktop session** and the optional
`requirements-automation.txt` dependencies. Linux keyboard access through the old
root-requiring `keyboard` package is replaced with unprivileged pynput. Native
Wayland applications do not expose the required global mouse/keyboard control;
Peter reports that limitation instead of pretending to automate them. Original
Mews coordinates still require calibration on the actual reservation screen.

AI Library keeps its existing staged indexing, FAISS retrieval, source selection
and separate worker architecture. It needs a real document source and a compatible
configured embedding model/environment; stale Windows document paths are cleared,
not replaced with invented Linux directories. Existing indexes must retain their
original embedding model. Sentence Transformers support remains optional through
`requirements-ai.txt`.

Local natural-language models can misunderstand dates or fail to follow tool
instructions. Review the actual saved change summaries and use undo. Microphone
quality and pronunciation affect transcription. Cancellation discards pending
voice output; an in-flight upstream synthesis request can finish in the backend.
Existing Notes/Calendar conflict detection and transactional rollback are retained.

## Verification and publishing

Run `.venv/bin/python -m pytest -q` after installing `requirements-dev.txt`.
Tests isolate their data and use synthetic local services; actual hardware/model
verification is reported separately in PETER_VALIDATION.md.

`.gitignore` and the source-only packaging allowlist exclude personal notes/tasks,
calendar, chats, memories, credentials, service settings, backups, runtime/cache,
model weights and generated media. The old directory had no `.git` metadata or
Git history. Publication starts with a new audited history. A private repository
is verified private before any source is pushed; the committed source/history is
scanned with Gitleaks. Future edits can still introduce private content: review
staged files before pushing.

The local animation assets and reproducible build are documented in [ORB_ASSETS.md](docs/ORB_ASSETS.md).
