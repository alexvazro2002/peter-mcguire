# Historical Windows documentation

This preserves the original NexusDesk 2.x documentation. For the current Fedora build, use README.md and LINUX_PETER.md. Old paths, bundled-data statements and Windows launch instructions describe the previous installation.

# Nexus Desk

## Workspace refinements

- Home's Today's focus lists all overdue tasks in red, then remaining plans today,
  then future plans, sorted by date and time within each group. Timed tasks become
  overdue after their scheduled time; all-day tasks become overdue the following
  day. Completed and unscheduled items are excluded. The list scrolls and refreshes
  each minute while Home is open.
- Navigation now follows Calendar, Tools, Notes, AI Library, Assistant. Notes uses
  a notepad icon.
- Notes Graph fills the remaining workspace, with the connections sidebar reserved
  for Read and Write. Retained drawing objects, a separate static background, and
  throttled drag events reduce redraw work. Resizing preserves pan, zoom, and node
  positions. Node dragging, wiki-link passage previews, and Reset view remain available.
- Tools uses compact cards grouped into Files, Media, and Automation, with category
  filters, search, sorting within each category, and Grid/List controls on the page.
  Individual tools place Setup and Process output beside each other when space allows.

## Update 2.3

Assistant edits now require executed write tools and verified persistence. Notes
opens Graph with connection previews and Custom-order dragging. Appearance adds
live colour sliders; the logo replaces the Home navigation button. Assistant has
read-only repository search, a shared model chooser, a reasoning speed toggle,
and optional ntfy answer notifications. See [UPDATE_2_3.md](UPDATE_2_3.md) for model
recommendations, setup, safe upgrading, changed files and limitations.

## Assistant (2.2)

A new Assistant chat uses the same local model as AI Library to search and update
the existing notes, lists, calendar and tasks through controlled tools. Multi-action
edits are staged together with rollback and recoverable undo. Read [ASSISTANT.md](ASSISTANT.md)
for tools, date handling, history counting, architecture, safe upgrade instructions
and model/testing limitations. Existing UI layout and v2.1 functionality are retained.

A unified Flet desktop workspace for your Notes, Calendar, utility scripts, and local AI Library. The two former application directories are now one codebase. Your large models and processed documents can remain in their current locations.

## Version 2.1

The existing navigation and workspace structure are retained, with slate surfaces, periwinkle actions, and mint note accents. Home now shows today's agenda, a weekly overview, upcoming plans, recent notes, and shortcuts. Tasks is replaced by a full calendar with Monthly, Weekly, and Daily views. The Library adds measurable embedding progress, direct passage search, keyword-plus-semantic ranking, source filters, and highlighted previews.

See [CHANGES.md](CHANGES.md) for design references and the search review. Existing custom accent colours are retained; the former default is migrated to the new palette.

### Updating a previous Nexus Desk installation

Extract this release to a new folder and copy your current `notes`, `tasks`, and `config.json` from the previous installation, replacing the bundled starter copies as needed. If you already have a `calendar.json`, copy it too. Keep a copy of the old folder, run `INSTALL.bat` in the new one, and verify Settings. The first launch imports the two existing checklists into Unscheduled once. It does not assign arbitrary dates or rewrite those Markdown files. Models, processed documents, and knowledge-base directories can remain external.

## Start on Windows

1. Extract the ZIP to a new folder, for example `D:\NexusDesk`. Keep the old folders while you verify the migration.
2. Run `INSTALL.bat` once. It uses **64-bit Python 3.12** and creates an isolated UI environment. Install Python 3.12 with the Windows `py` launcher if it is missing.
3. Open `START.bat`. `START_LIBRARY_AI.bat` also opens the same workspace for compatibility.
4. Open **Settings** and review the AI paths. Your default source is initially `D:\Universal_Library_AI`; your existing AI Python environment is initially `C:\paddle-env\Scripts\python.exe`.

The supplied Markdown notes and weekly checklist are included unchanged, with a new Home note. This archive contains your personal notes, including files named Credentials and Wifi. Keep it private.

To copy any newer data from the original panel, run this once from the new folder:

```bat
.venv\Scripts\python.exe scripts\migrate.py --panel "D:\PersonalControlPanel" --library "D:\Universal_Library_AI"
```

This copies Markdown notes/tasks, imports their ordering and usage settings, and records the existing library path. It backs up destination files it replaces. It never moves or deletes your original directories, models, or existing knowledge base. `migration-backups` can be used to undo imported file replacements.

## AI Library

There are two main actions:

| Action | Source used |
| --- | --- |
| **Launch** | The saved Default AI Library source in Settings. |
| **Choose Directory** | A browsed or typed folder, used for that session without changing the Launch default. |

Valid selections are a folder of processed `.txt` files, a library root containing `_KNOWLEDGE_BASE`, the `_KNOWLEDGE_BASE` folder itself, its `extracted` folder, or an existing `index` folder. Subfolders of text are supported. `.source` sidecars are used as source labels when present.

If a compatible FAISS index exists, it is reused without rebuilding. Both the original `metadata.pkl` format (primitive data only) and the new `metadata.json` format work. The saved embedding-model configuration is respected. When no index exists, the unified pipeline reads supported files and builds one. Short documents/paragraphs are preserved; long passages are bounded and overlap.

New indexes also save a source-file manifest. On later launches, added, edited, renamed, or removed documents trigger a new staged build. Existing legacy indexes remain usable and are updated only when you request it: stop the session, then use **⋯ → Update document index**. This is also the manual option after you add documents. Change detection uses file sizes and modification times; an external tool that deliberately preserves both may require a manual update.

### Processing feedback and search

The processing panel reports reading documents, loading the embedding model, embedding passages, saving the index, loading search, and connecting to the local model. Reading and embedding have determinate bars with completed/total counts. Embedding updates after each batch of up to 32 passages. Model loading, saving, and server startup have indeterminate bars because those stages do not expose a reliable percentage. Skipped files are listed in Session activity. Search/chat remain disabled until ready; Stop cancels the owned session.

Use **Ask AI** for an answer with numbered evidence, or **Find passages** for direct results without waiting for a generated answer. Both support a source filter. Find passages shows up to 20 ranked passages with source names, query highlights, excerpts, match types, and a full-passage dialog. Search timing is the worker's retrieval time, not total UI/network latency. Match types describe how a result was found; they are not confidence scores.

Type in the source menu to narrow its document list. The calendar's vault-note selector also supports this filtering.

Retrieval combines semantic FAISS results with an in-memory BM25 keyword index using reciprocal rank fusion. This retains topic search and gives exact names, identifiers, phrases, and document titles another way to surface. Diversification limits how many passages one source can contribute to a broad search. Filtering a single document searches that document even when it falls outside the global top results. A bounded cache avoids re-embedding repeated queries for the active index. No additional model or search dependency is required. Search quality with your real corpus/model still needs review; there is no claim of a benchmarked relevance increase.

New indexes are written inside `<selected folder>\_KNOWLEDGE_BASE\index`. Selecting an existing knowledge-base/extracted/index folder reuses that knowledge base's index location. Source documents are never changed. A requested rebuild keeps the former index in a timestamped `index.backup-*` folder. The index is prepared completely before replacing the old one. Skipped extraction errors appear in session activity and index configuration.

Supported input: text, Markdown, text-based PDFs, DOCX (including tables), EPUB, and HTML. Scanned PDFs must first be processed with your existing OCR workflow. This app can use its resulting text directory; it does not download or run PaddleOCR models automatically.

The same local Qwen / OpenAI-compatible server answers questions using retrieved evidence. Answers include numbered references and clickable source cards with the exact selected passages. Retrieval uses your question, rather than the former hard-coded procrastination query expansions. Evidence is limited by relevance and source diversity.

The first launch can take time if an index must be built or the model is loading. Activity and errors appear in the application. If you change sources, stop the current session, wait for it to finish stopping, then start the next one. Chat history is session-only. Clear conversation resets the display; each question retrieves fresh evidence and does not send previous chat turns to the model.

### Existing models and AI environment

No model weights are included in this ZIP. Set these paths in **Settings**:

- **Chat GGUF model:** your existing Qwen or Gemma model file. The launch chooser checks `ai_models` next to the app, `projects/ai_models`, the Desktop NexusDesk folder and LM Studio. Projectors are not chat weights. PaddleOCR is listed for OCR guidance, not general text chat.
- **llama-server executable:** an existing executable. Leave blank to detect one from PATH or LM Studio's backend folders. An already running local server can be used instead.
- **AI Python:** the interpreter containing FAISS and Sentence Transformers, normally `C:\paddle-env\Scripts\python.exe`. It runs in a separate process so the UI environment does not need Torch.
- **Embedding model:** your existing `all-MiniLM-L6-v2` cache, or an existing Sentence Transformers model directory. It must match the model used to build an existing index; matching dimensions alone do not guarantee matching embeddings.
- **Local base URL:** `http://127.0.0.1:8081/v1` by default. Port 8080 is left alone.

Offline models is enabled by default. No model download is attempted. If a dependency is missing, install `requirements-ai.txt` only in the existing AI environment:

```bat
C:\paddle-env\Scripts\python.exe -m pip install -r requirements-ai.txt
```

Only disable Offline models if you deliberately want Sentence Transformers to download a missing embedding model. Installing Python dependencies does not install Qwen or PaddleOCR weights.

If no healthy server is running, the app launches your existing llama-server with the configured GGUF. CPU is the portable default (`GPU layers = 0`); use `-1` or a positive layer count with a compatible GPU backend. Its log is `logs\model-server.log`. Startup timeout is 180 seconds by default (editable in config.json). The desktop app shares its owned server between Library and Assistant until the app closes or AI settings change. Stopping Library alone leaves Assistant usable. A pre-existing LM Studio/server session is left running. Standalone CLI sessions still stop their own server when they exit.

## Notes

Notes remain ordinary `.md` files in `notes`, or the vault selected in Settings. They can also be opened with Obsidian.

- **Read / Write / Graph:** Markdown preview, plain Markdown editor, and an interactive node graph.
- Debounced autosave, saving before navigation, **Ctrl+S**, and **Ctrl+L** to append a wiki link. Manually type links anywhere in the editor.
- `[[Title]]`, `[[Title|label]]`, and existing heading links are recognized. The graph/backlinks connect note titles; heading links open the note.
- Search matches titles and contents; pinned notes and custom/A–Z/Z–A/recent order are supported. Move up/down is in the note menu.
- Renaming updates wiki-link targets and backs up affected files to `.history`. Duplicate and Markdown export are available in the note menu.
- Deleted notes are moved to `.trash`, not permanently removed. Restore by moving the Markdown file back into the vault.
- Drag graph nodes to arrange them, drag the background to pan, scroll to zoom, and use Reset view to recenter. Only actual wiki links become graph edges.

This is a Markdown knowledge workspace, not a replacement for Obsidian's plugin ecosystem. Avoid editing the same note in multiple places at once. Stale editor buffers are detected; reloading after a conflict backs up your unsaved draft instead of overwriting a later Assistant/external edit. Changing the vault path never moves existing notes.

## Calendar and tools

Opening **Calendar** starts with a monthly grid that fills the remaining workspace. All views share the selected day and the same underlying items:

- **Monthly:** compact task/note chips, today highlighting, neighbouring-month dates, and overflow counts. Click a day for its Daily agenda. The number of visible chips adapts to window height.
- **Weekly:** seven day columns with separate all-day and timed sections, longer titles, details, and linked-note labels. Each column scrolls independently.
- **Daily:** complete titles, details, time, note links, task completion, editing, and recoverable deletion.

Use the view tabs, previous/next arrows, Today, or click the period heading to jump to a date. New item adds a task or note with a date, optional 24-hour time, details, and an optional vault-note link. The date field also has a picker. Empty date means Unscheduled; empty time means All day. The view filter searches titles, details, and note names; Show completed controls completed-task visibility. Times are wall-clock times on your computer; there is no timezone conversion, overnight duration, recurrence, reminder, or external calendar synchronization.

On the first launch, `notes\Tasks.md` and `tasks\Weekly Tasks.md` are copied into **Unscheduled**, preserving duplicates and completion states. Their Markdown files, headings, comments, and original text remain untouched. Exceptionally long titles are retained in full in item details. These are one-time copies: later calendar edits are stored in `calendar.json`, and later external checklist edits are not automatically imported. Back up that JSON alongside your vault. Deletion saves the previous calendar in `.history`; restoring that file restores its complete snapshot. Links to renamed/moved vault notes can be updated in the item editor.

All nine active utility tools are retained: FLAC Rename, File Converter, Compact Info, Find Duplicates, Path Map, Disk Heaviest, Strip All, Mews Segments, and Activity Script. The duplicate Tkinter shell, stale widget/theme modules, duplicate AI pipelines, and disconnected task/calendar prototype are replaced by the single working Flet application.

- FLAC Rename creates a fresh destination (numbered if needed), reads metadata with the former filename fallback, and copies into clean `Title - Artist.flac` names. It never renames pre-existing output files or originals.
- Find Duplicates still uses the 91% fuzzy music-filename rule. Scanning is read-only by default. Optional MP3 cleanup quarantines matching candidates under the source's `.duplicate-quarantine`, with `restore-manifest.json` recording their original paths. Review the report; similar names do not prove identical recordings.
- File Converter preserves existing output files. Video conversion requires an installed FFmpeg executable. Compact Info retains the original multi-format extraction and emits a numbered corpus index. Image OCR in this utility requires installed Tesseract; it is independent of the AI Library.
- Mews keeps the original desktop automation and its coordinate-selection workflow. Test it with your actual display scaling and Mews screen before letting it change reservations. It uses keyboard/mouse control and its original pause/quit controls.
- Activity Script keeps the original movement sequence, with interruptible waits and normal/Q/keyboard-interrupt key release. The workspace's Stop button sends a graceful stop request so held keys can be released. Forcefully terminating any mouse/keyboard automation outside this flow can bypass cleanup.

Tools run in background processes with an output log, Stop, and log export. Browse/Export use native Windows dialogs. The app supports dark/light appearances, accent customization, grid/list tool layouts, and usage sorting. Usage increments when a tool is actually launched.

## Structure and development

| Location | Responsibility |
| --- | --- |
| `main_flet.py`, `app/panel.py` | Entry point, navigation, app lifecycle, queued process events. |
| `app/views` | Notes, graph, Tasks, Library, Overview, tool forms, Settings. |
| `app/storage.py`, `app/calendar_storage.py`, `app/config.py` | Markdown, calendar data, and atomic settings writes. |
| `app/assistant`, `app/views/assistant.py`, `library/chat.py`, `library/runtime.py` | Controlled tools, journaled transactions/undo, chat, and shared local model transport/startup. |
| `app/tools.py`, `tools` | One tool registry and utility workers. |
| `library` | Source resolution, staged indexing/progress, hybrid retrieval, local server lifecycle, persistent AI worker. |
| `library_cli.py` | Command-line scan/build/chat using that same pipeline. |
| `tests` | Storage/calendar, progress protocol, retrieval, real FAISS, model/server doubles, and Flet constructor checks. |

The UI intentionally pins Flet 0.28.3. The original uploads mixed old and new Flet APIs; this version is implemented and tested consistently against the pin. Do not upgrade it to Flet 1.x without migrating the controls and file picker. The Windows launchers quote paths and resolve relative to their own location; Windows Terminal is no longer required.

For development:

```bat
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe main_flet.py --web --port 8550
```

The web mode is a local UI preview, not a hosted product. Enter native paths manually there; Browse/Export are available in the desktop app. Set `NEXUS_DATA_DIR` to isolate test data if desired.

Command-line access, from the existing AI environment and the new project folder:

```bat
C:\paddle-env\Scripts\python.exe library_cli.py scan --source "D:\Processed Text"
C:\paddle-env\Scripts\python.exe library_cli.py build --source "D:\Processed Text"
C:\paddle-env\Scripts\python.exe library_cli.py chat --source "D:\Processed Text"
```

The model generation and Windows desktop automation require validation on your own PC. The included validation report distinguishes the checks performed here from those requiring your existing models/backend/display.
