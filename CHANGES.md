# Nexus Desk 2.3

See [UPDATE_2_3.md](UPDATE_2_3.md) for task-persistence fixes, Graph-first Notes with
connection previews and custom dragging, appearance sliders/live preview, logo
Home navigation, read-only repository tools, model selection/recommendations,
extended-reasoning speed control and the privacy-conscious ntfy integration.

# Previous Release: 2.2

## Assistant

Added a persistent, separate Assistant chat using the same local model and settings
as AI Library. The model chooses validated tools for notes, day entries, tasks,
Markdown/plain lists, dates and historical questions. Multi-action requests stage
all writes, check for external changes, journal before/after images, roll back
failures and expose recoverable undo. Completed activities retain their occurrence
dates for complete-range counts; undated/uncategorized history is not treated as
known dated activity.

Model startup is shared by the app, so stopping Library does not stop Assistant.
Both use the common local HTTP transport. Existing CLI Library ownership remains.
The Notes editor preserves conflicting drafts; calendar editing preserves activity
metadata and rejects stale dialogs. The rest of the v2.1 layout/functions remain.

See ASSISTANT.md for architecture, all tools, upgrade steps and limits. See
VALIDATION.md for tests and actual browser checks. Live inference with the user's
large model was not tested; scripted model responses exercised the protocol/tools.

# Previous Release: 2.1

## Experience

The icon rail, workspace header, Notes layout, tool catalogue, and Library launch options are retained. Shared colours now use neutral slate surfaces, periwinkle for actions/tasks, mint for notes/evidence, amber for unscheduled work, and distinct success/error colours. Light mode uses corresponding darker accents for legibility.

Home replaces the large promotional hero with a useful daily overview: planned-today counts, today's tasks/notes, upcoming plans, clickable week dates, recent notes, AI status, and frequent tools. Empty states provide a clear next action.

The former Tasks checklists become a monthly-first calendar. Monthly, Weekly, and Daily offer progressively more detail, with shared navigation, dates, colours, filters, and the same item editor. Legacy tasks are copied into Unscheduled once; originals remain intact.

## Library review and changes

| Previous behavior | Change |
| --- | --- |
| Embedding was one blocking call with no completed-passage count. | Batches of up to 32 emit structured progress to the UI; stages without a measurable total remain indeterminate. |
| Only semantic retrieval; exact names depended on the embedding model. | Add BM25 keyword retrieval and reciprocal rank fusion while retaining the original embedding model and FAISS index. |
| Repeated searches re-embedded the same question. | Cache up to 32 query/source/result-limit combinations for the active index. |
| Finding information required a generated answer. | Add Find passages with direct ranked evidence results. |
| No document-level search filter. | Source filters apply to both direct search and AI answers. |
| Source presentation largely consisted of citation buttons. | Direct results show highlighted previews, source names, passage numbers, match types, timing, and full text. |
| Existing indexes were reused even after documents changed. | New-format indexes record file manifests and rebuild safely after changes; legacy indexes retain explicit Update document index. |

Relevance improvement is based on the added retrieval paths and deterministic checks, not a benchmark of your unavailable real models/corpus. Broad semantic searches can still produce weak matches. Review the full source passages and the AI's evidence.

## Design references

Reviewed on 30 September 2026. GitHub popularity was used to select established examples; these projects do not all provide numerical user ratings.

- [shadcn/ui](https://github.com/shadcn-ui/ui), approximately 125k stars, and its [calendar component](https://ui.shadcn.com/docs/components/calendar) and [blocks](https://ui.shadcn.com/blocks): quiet borders, consistent components, compact date navigation, and clear action hierarchy.
- [AFFiNE](https://github.com/toeverything/AFFiNE), approximately 73k stars, and its [product overview](https://affine.pro): a focused knowledge workspace connecting planning and notes.
- [Cal.diy](https://github.com/calcom/cal.diy), approximately 49k stars: shared calendar navigation, legible scheduling information, and restrained utility controls.

These are UX/design references. Their code, logos, images, branded assets, and exact layouts were not copied; the implementation remains native Flet.
