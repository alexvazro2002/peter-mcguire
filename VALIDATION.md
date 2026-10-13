# Validation - Version 2.3

Checked on 1 October 2026 with Python 3.12 and Flet 0.28.3.

**123 automated tests pass**, preserving the previous 90 checks. New coverage
exercises the packing task at Saturday 16:00, retries for unsupported success
claims, indirect write intent, clarification/no-op behaviour, read-back verification
and undo, repository search/read/page/path/secret/symlink boundaries, Graph-first
navigation, link directions/aliases/source lines, custom and filtered reordering,
disabled dragging under other sorts, hue/hex preview without premature persistence,
accent foregrounds, bounded graph height, logo navigation, GGUF discovery,
projector exclusion, shared-runtime switching, wrong/missing external models,
reasoning transport, preserved context/output settings, ntfy authentication,
destination validation, private answer defaults, disabled publishing and delivery
hooks for both AI sections while viewing another section.

`python -m pytest -q` and `python -m compileall -q app library scripts tests` pass.
Tests use temporary data and mock HTTP requests; no real notifications were sent.

Chromium browser checks at **1440 x 1000** and **1100 x 760** exercised the actual
Flet UI and a scripted local HTTP model, with isolated temporary data. The model
first falsely claimed the packing task was saved; the app retried, ran the tools,
saved the task and displayed it in the monthly calendar at 16:00. Disk JSON retained
all packing reminders. Checks also covered the launch chooser, an actual graph-line
click and its anchored preview, real note dragging with persisted order, logo Home,
and the live colour preview. Page and Flutter-console errors were checked.
Screenshots were inspected in `previews/update-*.png`.

Visual testing caught and fixed an incorrect second adjustment of Flet's normalized
reorder index, an Expanded child in a wrapping preview row, and graph clipping in
shorter windows. The complete browser workflow then passed without page/console
errors. The Notes graph also recalculates its layout when the app window resizes.

**Still requires testing on your PC:** real Qwen/Gemma inference and relative model
quality/speed, GGUF/backend compatibility, GPU offload, Windows native startup and
file dialogs, and actual ntfy/Windows notification delivery and permissions. No
model weights are present here; the large models were not uploaded or downloaded.
PaddleOCR image inference is not part of this text-chat feature. See UPDATE_2_3.md
for the full feature/setup report and limitations.

# Previous Release - Version 2.2

Checked on 1 October 2026 with Python 3.12 and Flet 0.28.3.

## Assistant Checks

**90 automated tests pass**, including all 41 existing regression checks. New
coverage includes the five-change example as one operation and its single undo,
general book/list/note/task actions, article/spelling identity, alphabetical
ordering, reordering, plaintext and nested lists, checklists, date/weekday/year
boundaries, ambiguous dates, all-record counts beyond a results page, undated
history, planned-task exclusion, cross-linked activity deduplication, metadata
preservation, destructive recovery, disk-failure rollback, interrupted-operation
recovery, optimistic conflicts, preserved editor drafts, malformed model replies,
tool/argument/path boundaries, cancellation, local-only transport, unsupported
JSON-mode fallback, shared/external model ownership and conversation persistence.

`python -m pytest -q` passes. `python -m compileall -q app library scripts tests`
passes. Tests write to temporary data directories, not the bundled personal notes.

Chromium checks at **1440 x 1000** and **1100 x 760** exercised the actual Flet UI:
mouse and Enter-to-send, visible processing, the five-change summary/source links,
persisted Markdown/calendar contents, whole-operation undo and subsequent chat.
No page errors were recorded. Screenshots are in `previews/assistant-*.png`.
The UI fixture used a scripted local HTTP model and isolated sample data; it did
not use or mutate the user's real files. A Flutter layout-constraint error found
during visual testing was fixed and the workflow rerun successfully.

**Live Qwen inference was not tested:** the user's model files/backend are not
present. These tests validate orchestration, storage, safety and UI, not the real
model's ability to interpret every natural-language request. Windows native
startup still needs verification with the user's existing executable/environment.
See ASSISTANT.md for counting/date, prompt-injection and concurrency limitations.

## Previous Release Validation (2.1)

Checked on 30 September 2026 with Python 3.12 and Flet 0.28.3.

## Automated checks

**41 checks passed**, including the previous storage, utilities and FAISS checks and the new calendar/search/progress coverage. Python compilation also passed for the application, AI pipeline, utilities, migration/packaging scripts, and tests.

- Calendar: one-time legacy import; duplicate and completed tasks; preservation of original files and long task text; scheduling, editing, completion, deletion backups; invalid/corrupt-data preservation; leap-year and year-boundary navigation.
- Notes/checklists: safe paths, empty drafts, wiki links, backlinks, rename backups, recoverable deletion, and preservation of headings/comments/duplicate task titles.
- Indexing: real FAISS with deterministic embedding doubles, staged replacement/backups, failure preservation, source manifests/change detection, short and long passages, legacy metadata, and directory forms.
- Progress/protocol: completed/total counts across multiple embedding batches; structured worker events; ready/source metadata and direct passage results.
- Search: exact identifier/name retrieval, keyword and semantic ranking paths, source filtering, diversity/deduplication, repeat-query caching, and simulated chat evidence/payload.
- UI: actual Flet constructors for every view and nine tool forms, dark/light settings, calendar tabs/editor/date picker/Unscheduled, Monthly as the initial Calendar view, and determinate embedding display.
- Utilities: actual image conversion with existing-output protection; FLAC filename fallback, original-file preservation, and a fresh output directory.

Run `python -m pytest -q` from the project directory after installing `requirements-dev.txt`. Tests isolate their data from the bundled personal notes.

## Visual and interaction checks

The app was rendered in Chromium at 1440 × 1000. Home, all three calendar views, the item editor, embedding progress, direct search, Notes/graph, Tools, Settings, and directory selection were inspected. The monthly calendar was also inspected at the 1000 × 680 minimum window size and in light mode. Browser captures recorded no console or page errors.

Creating and saving a calendar task through the rendered interface was checked against the saved JSON. Day-cell and Monthly navigation were also exercised. Preview calendar items/documents and the illustrated progress/search state use isolated example data. Screenshots are illustrations of the interface, not a real-model performance benchmark.

## Checks needed on your PC

The large models and Windows runtime remain external. Full Qwen generation, the installed embedding cache, llama-server/backend detection, GPU behavior, native file dialogs, and mouse/keyboard automation require validation on your PC. Real-corpus search relevance and speed have not been benchmarked here.

Review Settings, launch your existing library, then test a small processed-text directory. Watch embedding counts advance, search a known phrase/name, try a source filter, and verify the full passage and AI citations. Keep your existing folders while validating the update. Back up `calendar.json` with your notes.

The packaging script checks ZIP integrity and reports its SHA-256 digest. Runtime environments, caches, generated settings/calendar data, logs, and model weights are excluded.

## Previews

| Screen | Preview |
| --- | --- |
| Home | [overview.png](previews/overview.png) |
| Monthly calendar | [calendar-monthly.png](previews/calendar-monthly.png) |
| Weekly calendar | [calendar-weekly.png](previews/calendar-weekly.png) |
| Daily calendar | [calendar-daily.png](previews/calendar-daily.png) |
| Calendar editor | [calendar-editor.png](previews/calendar-editor.png) |
| Light calendar | [calendar-light.png](previews/calendar-light.png) |
| Minimum window | [calendar-small.png](previews/calendar-small.png) |
| Embedding progress | [embedding-progress.png](previews/embedding-progress.png) |
| Passage search | [library-search.png](previews/library-search.png) |
| AI Library | [ai-library.png](previews/ai-library.png) |
| Choose Directory | [choose-directory.png](previews/choose-directory.png) |
| Notes | [notes.png](previews/notes.png) |
| Notes graph | [notes-graph.png](previews/notes-graph.png) |
| Tools | [tools.png](previews/tools.png) |
| Settings | [settings.png](previews/settings.png) |
