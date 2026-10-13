# Personal Memory

Personal memories are explicitly supplied facts and preferences in the local file
`personal-memory.json` beside the application's configured data files
(`NEXUS_DATA_DIR`, or the project directory by default). The dashboard and local
Assistant use this same store. Existing chat transcripts are not imported, and
no example profile or inferred personal facts are inserted.

The schema is `{ "schema_version": 1, "memories": [...] }`. Each entry has a
permanent random `id`, plain-text `content` (1–4,000 characters), `category`,
`source` (up to 300 characters), timezone-aware `created_at` and `updated_at`,
and boolean `temporary` and `pinned` flags. Categories are `personal`,
`preferences`, `work`, `projects`, `devices`, `decisions`, `goals`, `temporary`.
The category is descriptive; the separate `temporary` flag controls clearing.
Temporary entries remain until explicitly cleared or deleted; they do not expire
silently. Pinning changes presentation and context priority, not permanence.

## Historical web API contract (not served by the native app)

The table below preserves the earlier web dashboard contract. This checkout has no router serving these endpoints. Peter's native `app/views/memory.py` dashboard directly uses the same validated `MemoryStore`; it supports search/category/permanence/pinned filters, add/edit, confirmed deletion, clear temporary and export. The Assistant memory tools use this same store and transaction journal.

| Method and route | Request / response |
| --- | --- |
| `GET /api/memories?q=&category=` | `{memories: [...], categories: {key: label}}`; empty query lists all stored entries. |
| `POST /api/memories` | JSON `content`, optional `category`, `source`, `temporary`, `pinned`; returns the saved entry. |
| `PATCH /api/memories/{id}` | JSON containing only the fields to change; omitted values are preserved. |
| `DELETE /api/memories/{id}` | JSON `{confirmed: true}`; the dashboard asks for confirmation before sending. |
| `POST /api/memories/clear-temporary` | JSON `{confirmed: true}`; removes all temporary entries, including pinned ones. Permanent entries are retained. |
| `GET /api/memories/export` | `{content: "..."}` with the actual complete store serialized as JSON, including an honest empty array for an empty store. |

The dashboard searches content, source and category label; it also filters by
category, permanence and pinned state. Export always includes the complete store,
even when dashboard filters are active. Dates shown in the dashboard use the
browser's local timezone.

## Assistant integration

`search_memories`, `read_memory` and `save_memory` are registered local Assistant
tools. The Assistant instructions require explicit user intent to save or correct
a fact, prohibit inferred facts, and treat retrieved memories as untrusted data.
Saving a memory uses the existing transaction journal: writes are staged, committed
only after a successful response, recoverable after interruption, and undoable
with the rest of the Assistant operation. A concurrent dashboard edit prevents a
stale Assistant transaction or undo from overwriting it. Dashboard deletion and
clear actions require confirmation and are not exposed as model tools.

At request time a bounded, deterministic selection of pinned and word-matching
memories is available as context. There is no model call for searching, managing,
or exporting memories. The Assistant can request more real entries through its
tools. Running the Assistant itself continues to use the configured local model.

## Validation and local safety

The store accepts only the documented fields, validates booleans, lengths, IDs,
timestamps and categories, normalizes text, and rejects active HTML, control
characters, recognizable credentials, authentication tokens and hidden prompt
markers. The UI inserts memory content as plain text. Persisted records and
exports are validated too, so invalid data is never silently displayed or
overwritten. Secret rejection errors do not echo rejected content. These pattern
checks are not a substitute for avoiding arbitrary confidential data in memories.

Writes are atomic; store access is synchronized with Assistant transactions.
Malformed existing files are preserved and reported as errors. An absent file
represents an empty store and remains absent until the first real write. The
storage file cannot be a symlink. Tests use isolated temporary stores and scripted
Assistant responses; they do not start a model or modify user data.
