# Nexus Desk Assistant

Version 2.3 adds verified edit completion, read-only project tools, a launch model
chooser and ntfy notifications. See [UPDATE_2_3.md](UPDATE_2_3.md) for current setup,
speed recommendations and model compatibility. The remaining architecture below
continues to apply.

## Using it

Open **Assistant** in the navigation rail and send a message. It connects to the
same local model configured for AI Library, without needing a document index or
an active Library session. There is no separate Assistant model or cloud provider.
Existing defaults point to the Qwen3.5-4B GGUF and local llama.cpp/LM Studio server.
An already running endpoint is reused, just as it is for Library.

Settings now includes an Assistant timezone, defaulting to `Europe/London`.
Use the existing **Context size** and **Answer token budget** settings for both
sections. The default 16,384-token context is recommended for tool schemas and
evidence. Increase the output budget if your model spends its response on reasoning
instead of returning a complete JSON response.

Questions are answered from live app data. Requested modifications are performed
without an extra approval screen; genuine ambiguity prompts a clarification.
Progress shows the current tool/status. Completed changes are listed accurately
from the tools' results. Source buttons open notes or the corresponding day agenda.
The undo icon on a response restores the entire operation, not just its last edit.
The history icon also exposes operations from previous app sessions. Undo itself
creates a recoverable operation, so undoing that operation restores the changes.
New conversation clears chat, not app data or change history.

## Architecture

- `library/chat.py`: common HTTP chat transport, used by Library and Assistant.
- `library/runtime.py`: one app-owned local model process shared by both sections.
- `app/assistant/engine.py`: bounded multi-round model/tool loop with JSON responses.
- `app/assistant/tools.py`: validated, allowlisted workspace actions and searches.
- `app/assistant/transactions.py`: staged changes, conflict checks, journal, rollback,
  crash recovery and undo.
- `app/assistant/lists.py`: Markdown/plain-list editing while preserving surrounding
  prose, nested items and headings.
- `app/assistant/dates.py`: dateparser plus explicit relative-weekday semantics.
- `app/assistant/service.py`: background work, cancellation, event queue and local chat.
- `app/views/assistant.py`: the Flet chat view, using the existing palette/controls.

The model chooses tools over multiple rounds; there is no application parser
matching the example's movie names or message wording. Tools/results are described
in the model's system prompt. A JSON envelope works even on local servers without
native function calling. Native tool-call responses are accepted too. HTTP JSON
mode is used when supported, with prompt-based JSON fallback for older endpoints.
Every argument is validated before its handler runs. Unknown tools, unknown
arguments, invalid dates and paths disguised as note titles are rejected.

Notes and lists remain in the existing `Vault` as Markdown. Tasks and day notes
remain in the existing `CalendarStore` and `calendar.json`; IDs, imported tasks,
links and other existing fields are retained. There is no second note/task/list
database, embedding requirement, file-execution tool or unrestricted filesystem API.
Calendar changes use its validation and persistence API, with a single final write
for the whole staged calendar rather than repeatedly saving individual entries.
CLI Library startup still manages its own model when not launched by the app.

## Tools

| Area | Tools |
| --- | --- |
| Discovery | `workspace_overview`, `resolve_date` |
| Project (read-only) | `list_repository`, `search_repository`, `read_repository_file` |
| Notes | `search_notes`, `read_note`, `create_note`, `edit_note`, `delete_note` |
| Calendar | `search_calendar`, `read_calendar_entry`, `create_calendar_note`, `update_calendar_entry`, `delete_calendar_entry` |
| Tasks | `search_tasks`, `create_task`, `edit_task`, `complete_task`, `delete_task` |
| Lists | `find_list`, `read_list`, `add_list_item`, `remove_list_item`, `move_list_item`, `transfer_list_item` |
| History | `search_history`, `count_history` |

An edit can append/prepend, replace one exact passage, or explicitly replace all
contents. List insertions support alphabetical/top/bottom ordering; reordering
supports top/bottom/zero-based positions. Alphabetical sorting ignores leading
articles and case. Very close unique spelling matches can reuse an existing title;
competing matches require clarification. A missing removal is a no-op, and an
existing item/task is not duplicated. Notes with multiple list headings require
the model to select the relevant section. Unclear plain-text layouts should be
handled through exact note edits rather than blindly reordering prose.

## Example

For a message anchored to **1 October 2026**, in the configured local timezone:

1. `resolve_date` establishes October 1, upcoming Sunday **October 4**, and
   yesterday **September 30**.
2. The model searches and reads the existing watchlist and completed-film list.
3. It stages a late-work day note for October 1 and `Watch Interstellar` for October 4.
4. It inserts Interstellar alphabetically into the watchlist.
5. It transfers the matching canonical film title to Films I've seen, removing
   it from the watchlist if present and retaining its September 30 occurrence date.
6. A final model response commits all five changes as one operation. A model,
   validation, context-budget or storage error before completion saves none of the
   staged changes. If the model asks for clarification, pending changes are discarded.

The five actual changes, including no-op differences when an item already exists,
are reported by the application. This workflow was tested with scripted model
responses through both the engine and the actual UI/HTTP transport. Live Qwen
interpretation still needs to be checked with your existing model files.

## Dates And Counting

An explicit date in a message anchors its relative dates; otherwise the Assistant
receives the current local timestamp. `this Sunday` is the next Sunday including
today when already Sunday. `last Friday` is strictly before the anchor. Numeric
dates such as `01/10` require clarification rather than a US/UK guess. Dates without
a year use the anchor year, and ordinary relative dates use dateparser.

Completed list activities can carry a small hidden Markdown comment, for example:

```markdown
- The Peanut Butter Falcon <!-- nexus-assistant: {"date":"2026-09-30","activity":"watch"} -->
```

This stays in the existing Markdown file and is hidden by normal Markdown rendering.
Calendar observations can hold `metadata` with normalized activity, subjects,
people and duration. Repeated activities should create a new dated calendar record
instead of duplicating an item in a completed-items list.

Historical tools scan all live records, with pagination for evidence and full-range
counts instead of counting only top search matches. Planned tasks are excluded from
activity history by default; `include_tasks` permits intentional task statistics.
Matching list/calendar references to the same dated activity are deduplicated.
Distinct calendar occurrences, including repeated events in one day, remain countable.
ISO dates in Markdown lines or dated headings provide additional historical evidence.

**An undated note cannot prove when something happened.** The tools report undated
matches separately. Activity filters count explicitly classified activities only;
the model is instructed to review older free-text data separately and describe gaps.
File modification times are never used as event dates. Free-text search is ranked
lexical search; the model can issue additional searches for alternate words. Deleted
records are recoverable through undo/trash, but are not included in live-data counts.

## Safety And Recovery

Each message gets an in-memory view of existing storage. Writes stay staged while
read tools see earlier staged changes. Immediately before saving, the operation
checks the note catalogue, all read notes and calendar against their original
contents. Concurrent external edits cause a conflict rather than an overwrite.
The Notes editor also detects stale buffers after Assistant/external edits.

The write-ahead journal is stored in the active vault's `.history/assistant`.
It contains before/after images and operation status. Each file is written through
existing atomic storage helpers. A normal failure rolls back already written files;
an interrupted operation is recovered at Assistant startup. Deleted notes also use
the vault's existing `.trash`. Keep these local backups if you need to undo changes.
They can contain sensitive note contents, like the app's other existing backups.

Undo checks that affected files still match the operation's result. It will not
overwrite a later manual edit. Undo later conflicting operations first, or restore
a before-image manually. A crash-recovery conflict preserves the journal and blocks
new Assistant requests pending repair. Invalid conversation JSON is also preserved
instead of silently overwritten.

This is an optimistic, journaled transaction for a local single-app workflow, not
a database-wide lock across external editors. Other processes can briefly observe
intermediate writes. Avoid editing the same files during a commit/recovery. Cancel
discards staging at the next safe point; an already completed commit is undone with
Undo instead. Closing the app cancels outstanding Assistant work and stops only its
owned model server; externally started servers are not killed.

Chat is persisted locally in `assistant-conversation.json` (last 200 messages, last
40 considered as model conversation context, retaining complete recent turns
within the available input budget). Daily restart greetings and app errors do
not consume that history. Structured source references and executed search/action
metadata accompany the context; older assistant prose is never proof of live facts.
The file is ignored by git and packaging.
App data stays accessible from any view, while progress/results are collected by
the existing UI poll loop. No controls are updated from model/background threads.

## Upgrade

Do not extract over your personal data without a backup. Extract the ZIP to a new
folder, install requirements, and either point Notes vault to your existing vault
or copy your existing `notes`, `tasks`, `calendar.json`, and `config.json` into that
folder. Preserve `.history` and `.trash` when moving your vault. Alternatively set
`NEXUS_DATA_DIR` to your existing data directory. Models remain at their current paths.
Bundled notes are existing project templates, not replacements for your own data.
Rerun `INSTALL.bat` (or install requirements.txt into your UI environment) for the
new dateparser/tzdata dependencies. No model download is required.

## Limits And Validation

Tool planning, inference and semantic intent resolution depend on your local model;
the application cannot guarantee that a small model will interpret every request.
It validates tool arguments and protects storage, but does not prove a model's
semantic plan matches every nuance of the user's intent. Review the change summary
and use undo for a mistaken interpretation. Retrieved data is explicitly untrusted
in the prompt, but prompt-injection resistance is not a formal security guarantee.

Requests are bounded to 8,000 characters, 20 rounds and 64 total tool calls, with
8 calls per round. A context-size guard rejects overlarge conversations/results
rather than dropping evidence silently. Split very broad requests or increase the
model context in Settings. Large note replacements require smaller exact edits.
Calendar recurrence, timed notifications, attachment OCR and automatic bulk
classification of old undated notes are not added by this feature.

See `VALIDATION.md` for tests and the screenshots in `previews/assistant-*.png`.


## Evidence and complete requests (October 2026)

Peter's behaviour instructions live in `app/assistant/engine.py`,
`conversation.py`, `personal_grounding.py` and the dedicated email reader.
These are runtime instructions and checks; editing this document alone does not
change his behaviour. Personal claims require explicit user statements or a read
personal record. A previous assistant reply, filename, opened file or job advert
is not evidence of the user's CV or qualifications. Unsupported claims are retried
with a correction, then replaced with an honest explanation if still unverified.
Old unsupported personal claims are omitted from model history, while the visible
conversation remains intact for review. These checks reduce specific failure
modes; free-form model output is not a guarantee of universal factual accuracy.

A yes/yeah reply to a verified email reading offer fetches the real body before
answering. CV questions end the email topic; job requirements do not establish the
user's experience. Gmail permissions remain read-only.

`completion.py` checks explicit named-note payloads and requested dated task slots
before commit. An empty note, a missing observation/time, conflicting times or a
missing task occurrence causes a correction; unresolved requests save nothing.
Saved notes use readable Markdown even though spoken replies use plain sentences.
Corrections about forgotten note text recover the earlier requested payload and
edit the existing note rather than attempting to create it again.

For the fully recognised combination of scheduling one uniquely identified
unscheduled task at explicit weekday/time slots and creating a named timed journal
note, the app builds the actions directly from live data. It preserves the original
task ID/details, creates additional occurrences, and formats the observations with
dates and times. Every clause must match and the existing task must be unambiguous;
other requests use the model/tool loop with the same transaction checks. No partial
parser executes extra clauses. All changes retain the existing journal and undo.


## Complete email subject lists

Routine requests to list/read email subjects use `mail_subjects.py` and a dedicated
read-only `GmailClient.list_headers` operation. The reply contains the actual
subjects in the same request, without model inference or a placeholder promising
later work. Common email spelling errors are normalised. Subject-only requests do
not select an email body or ask which of several messages to read.

A local-day query uses timezone-aware midnight boundaries and enumerates all Gmail
pages, rather than treating the tool planner's four-row preview as a complete list.
A requested recent count (for example five) is honoured. No bodies are fetched, mail
is not marked read, and normal Gmail read-only permissions remain unchanged.
Unsupported filters or additional actions stay in the ordinary tool planner; this
routine never silently drops them. Cancellation, API errors and incomplete results
cannot become a successful subject list. The final-reply guard also rejects
announcements such as “give me a second to fetch them” as a completed email answer.


## Action reliability and prepared email reviews

Peter is a local application using the selected LM Studio model. Instructions are
system-prompt text, tool schemas and actual retrieved results. NexusDesk does not
load Codex SKILL.md files or acquire a new brain from a behaviour document. Ordinary
workspace planning still uses the model; application code executes and verifies
writes. General dialogue is not an execution engine or a background task queue.

Explicit deletion of all unscheduled tasks now uses `workspace_actions.py`: inspect
the full live calendar, select only tasks with no date, commit once, verify and
retain undo. Dated all-day tasks remain dated. Requests for a Gmail link select
validated stored message metadata and return an actual source button immediately.
Promised actions and invented workspace searches fail reply checks.

`mail_flow.py` prepares bounded email reviews by local date/period, with optional
fuzzy From/Cc matching (spacing, case, accents and close spelling). It fetches full
messages, inventories and downloads attachments, and prepares an expiring private
cache using AI Library extraction, chunking and retrieval. The report and texts can
be opened from chat history in AI Library. No email or attachment instructions can
execute tools. Only gmail.readonly is requested; sending/modifying mail is absent.

The cache has a 24-hour validity window and is cleaned up on subsequent cache use.
Literal email readings select checked original sentences. Summary and translation
requests instead use short paraphrases of the actual prepared evidence, followed
by a factual and language check before delivery. Each requested email gets its own
summary, with evidence kept separate by message. A separately labelled assessment
is available when asked. Unknown/unreadable attachments remain explicit;
scanned PDFs without text require opening/checking in Gmail. PDF, DOCX, text, HTML
and EPUB extraction is supported. Limits are explicit: 300 matched messages per
review, 20 MiB per attachment and 100 MiB per collection by default. Truncation,
failed downloads and incomplete participant headers are reported, not interpreted
as proof of no messages or no attachments. Unsupported extra filters prompt a
clarification rather than being silently discarded.

A short social prompt avoids sending the full workspace catalogue for greetings
and thanks. `library/chat.py` records numeric request timings and token counts,
without logging message contents. More context and extra model/tool rounds increase
latency; they do not change the model's underlying capability. Email reports and
clear bulk actions do not require model inference. Detailed email questions use a
small evidence selection prompt on the same local model.

## Natural speech and automatic action recovery

The saved reply and its captions retain the written date, time and source links.
Only TTS input is normalized: British day/month dates, ISO dates, clock times,
currency, Markdown and links become natural speech without spelling punctuation.
For example, `10/10/2026 at 14:15` is spoken as “October tenth at quarter past two
in the afternoon” when the current year is 2026. Sentence audio remains generated
ahead and played sequentially, with captions appearing when each sentence starts.

English and Spanish use the same configured British male speaker embedding,
`bm_george` by default, with identical speed and pitch processing. Spanish changes
only the phonemizer language. Detection runs locally per
sentence, using lexical clues and bundled English/Spanish language profiles;
uncertain short fragments retain the configured language. This changes pronunciation,
not the email's language or content. No email text is sent to a speech cloud service.

Long email reviews first queue a brief professional acknowledgement. Quick workspace
changes have no spoken preamble. Other requests still running after seven seconds
receive one. These are progress
events, not completed answers or extra conversation-history entries. The final
answer queues after the acknowledgement instead of cancelling it. Review periods
include calendar months, with month-end dates clamped to the last valid day.

A clear request to create a dated reply task from the current email uses the actual
stored email reference, date/time validation and one undoable transaction, including
the real Gmail URL in task details. Dictation such as `U8PM` is accepted as `8pm`.
Conversational introductions such as “And Peter” and dotted times such as `9.13 pm`
are supported, with the same request going directly to a verified task at `21:13`.
Repeating the same request does not create another identical task. Ambiguous targets
require clarification; additional work continues to the general planner.

If a model repeatedly fails its action protocol, the app retries the full original
request once with a different installed LM Studio instruction model. Only failures
before a transaction commit can trigger this retry. Staged primary-model writes are
discarded, and storage/commit failures do not cause a second write attempt. The
normal model choice remains unchanged. A successful retry reports verified saved
changes; if both models fail, the app reports that nothing was saved.

Model planning is limited to 45 seconds and eight inference calls per attempt;
each HTTP response wait is bounded by the remaining budget and at most 30 seconds.
The fallback has a fresh bounded attempt, then reports failure rather than looping
for minutes. Cancellation and abandoned staged writes still cannot become commits.
Fallback models loaded by Peter are released before the terminal response, freeing
GPU memory for ordinary answers. User-preloaded models are not unloaded.

The desktop window starts maximised with its normal frame and title bar. Peter's
own replies use a professionally warm, lightly playful butler manner; routine
confirmations and greetings no longer append “sir”. Literal quoted mail is preserved.

## Separate task wording from its schedule

In an explicit creation such as “Add a task for 21:00 to finish my cooking before
20:22”, the schedule is 21:00 and the task wording retains “before 20:22”. A clock
inside the description does not replace the scheduling clock or cause an automatic
contradiction prompt. Time-only scheduling uses the local current day and states
that date in the confirmation; explicit days use the normal date resolver.

Common, fully understood task-plus-note creations use one checked transaction,
without model planning. Unquoted note titles and `include`/`including` retain the
entire requested payload. Short recognised ingredient lists become Markdown with
one bullet per ingredient, preserving garlic powder and an explicit black pepper
clarification. Unknown ingredient phrases or extra actions continue to the general
planner; no recognised prefix is saved as a partial completed request.

Completion checks preserve multiword ingredients, accept useful subject headings,
and reject a newly created note containing an identical repeated section. Existing
different note contents are preserved for normal read-and-merge planning. Repeating
an already completed identical task-plus-note request is a no-op, and a saved pair
can be undone together. Long note requirements are compacted only in model guidance;
the complete user payload remains available to execution and completion checks.


## Stopping Peter and speaking over him

The Home and conversation views include a labelled **Stop Peter** button. It
stops pending reasoning, queued speech and playback, and immediately lets you
enter another request. In manual mode, **Interrupt & speak** stops the current
request and opens the microphone. With **Hands-free** enabled, start speaking to
interrupt Peter; the opening words remain in the captured request. Saying just
“Peter, stop” stops him without asking the model another question.

Cancellation uses a separate token for each request. A stopped request cannot
resume when the next request starts, publish a late answer, or commit staged
changes. A save that completed just before Stop remains recorded in chat and can
be undone. Cancellable model connections run in a private HTTP worker so Stop
also closes the inference socket, including during model discovery. Cold model
startup observes the request token. Shared LM Studio models remain available.

The narrow task-and-note route tolerates an accidental trailing double quote or
quotation marks around the whole command. Balanced quotes inside titles and note
content are preserved. Repeating an already saved cooking/Recipes request returns
its existing result without adding duplicate tasks or ingredients.
