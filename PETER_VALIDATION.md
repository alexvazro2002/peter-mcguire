# Peter (McGuire) — verification record

Checked 13 October 2026 on this Fedora installation. This record separates actual
local API and installation checks from tests using synthetic services and data.

## Responsiveness update

Peter now uses npm thinking-orbs 0.3.1 through its actual exported canvas engine.
Eight dark/light animations are rendered to lossless native-client WebP assets
with 144 frames each; idle/listening/thinking/speaking play locally without Python
frame polling. Typed requests, speech transcripts and Peter replies remain in the
scrollable Home conversation, including the minimum window size.

The initial Assistant prompt was reduced from 18,861 to 7,466 characters
(60% smaller). Exact tool schemas are discovered only when needed. Ordinary
answers are capped at 384 tokens, with one or two short sentences requested.
Historical conversation excerpts are bounded and oldest whole turns are evicted
from model input, while saved messages remain available in the interface.
Current user requests and pending tool evidence are retained; read results use
truthful smaller pages.

Gemma Vulkan cold startup succeeded in 7.268 seconds. Five consecutive real
Assistant requests passed, using 1,438–1,604 prompt tokens; warm replies took
1.819–2.620 seconds before the final reasoning fix. With the final reasoning-off transport, three consecutive chat replies took
1.554, 0.967 and 0.919 seconds, using 1,726–1,812 input tokens and zero hidden
reasoning tokens. No context overflow occurred. A real unscheduled-task write
completed in 4.680 seconds including one model-argument retry; its empty date/time
were verified in storage and Undo removed it. A grounded historical readback
then correctly named the task in 2.195 seconds across two model rounds, without
creating another task. For the same math request,
LM Studio ignored the template-only reasoning flag and used 98 hidden reasoning
tokens (4.181 seconds). `reasoning_effort="none"` reduced that to zero hidden
reasoning tokens and 1.981 seconds. These are measured samples, not latency
guarantees. The CPU model load failed; GPU offload is now configured.

## Application and regression checks

The installed Desktop application uses a fresh Python 3.11.16 environment and
Flet 0.28.3. The final NexusDesk test suite passes: **298 tests in 16.38 seconds** in the installed Python 3.11 environment. Tests are restricted to `tests/`
so the separately retained Stirling-PDF source does not enter test discovery.
Coverage includes existing Notes/Calendar/Memory/tools/indexing behavior, validated
Assistant writes and rollback/undo, local model discovery/authentication,
voice transport and cancellation, startup process ownership, source packaging,
and the empty Notes interface.

Installer tests verified replacement of an incompatible Python environment and
rediscovery of an existing uv-managed interpreter. Repeated private native-library
setup succeeds; Flet's transitive shared libraries resolve without system changes.
Desktop and application-menu launchers point at the installed project and exist
with executable permissions. `./start-linux.sh --diagnose` reports native-library,
voice-runtime, voice-API and model-path readiness. The installed native client is
running; a second launch exits successfully with the existing-window message.

## Real Gemma and saved edits

LM Studio's actual CLI and API report the already installed
`google/gemma-4-e4b@q4_k_m`, original API identifier `google/gemma-4-e4b` (cold-start alias `peter-gemma`), at loopback port
8081 with an 8,192-token context. `ModelRuntime.ensure()` and actual inference
succeeded without downloading or unloading Gemma weights.

A previous real multi-action request created a note, calendar task and memory,
and transaction undo restored all three. It assigned a date despite the request
saying unscheduled; that result validates executed storage/undo, not perfect
natural-language scheduling. Task instructions now explicitly require an empty
date for unscheduled tasks. Saved assistant history is replayed in the same JSON
format as the active protocol. Real synthetic-history follow-up conversations
returned natural answers with no workspace writes.

A rendered-interface request created `Peter UI Check` with the exact text
`Interface validation complete.`. The saved Markdown was checked, the change
summary and source link appeared, and clicking Undo removed the note from disk.
This test used isolated data, not the original personal workspace.

## Real VoiceStudio checks

VoiceStudio 0.5.6 is installed separately, with an isolated CPU backend. Its model
API confirms complete supported installations of KittenTTS mini 0.8 and Whisper
base. Re-running the model installer reused both installations.

Actual `POST /v1/audio/speech` generated a 333,244-byte mono PCM WAV at 24 kHz,
6.942 seconds long, in 5.565 seconds. Actual `POST /v1/audio/transcriptions`
returned all words of the synthetic input in 1.383 seconds (punctuation changed).
These are observations of one short CPU run, not general speed guarantees.

The application's VoiceController used those real APIs for startup synthesis
and synthetic capture/transcription. Listening, thinking, speaking and idle
states progressed correctly, and adapter temporary audio was erased. Capture and
playback were substituted in that adapter check: no user microphone was recorded,
and physical speaker quality was not assessed. Native PipeWire/ALSA recording and
playback executables are installed. Startup greeting remains enabled.

## Interface checks

The actual Flet interface was inspected in the browser preview at 1280×720,
1000×680 and 1440×900. The compact and normal Home layouts, animated orb, typed
submission, conversation, source/change summary, undo and empty Notes view were
checked. No browser error/warning messages were captured. All view/tool controls
also construct in the automated tests, including dark/light modes and the minimum
window size. Browser previews use the same Python/Flet views as the native app;
native file dialogs and physical microphone quality still need user review.

## Original data cleanup and publication

At migration time, all 20 files in the original Notes vault, including note
metadata, managed history and trash, were permanently removed. No note-content
backup was created. Old assistant conversation and stale note navigation data
were cleared. All eight existing calendar task records and the weekly task file
were verified preserved; documentation and memories were retained. New notes
created afterward are ordinary user data and are not part of that cleanup.

The original application had no Git metadata/history. Source publication uses a
new history and an explicit source/document allowlist. Notes/tasks, calendar,
conversation, memories, credentials, service settings, runtime/cache, models,
media, vendor projects and backups are excluded. Gitleaks source and committed-history scans are completed before pushing; the
destination is verified private before push. The private repository is
[alexvazro2002/peter-mcguire](https://github.com/alexvazro2002/peter-mcguire). Source-package ZIP integrity is checked separately.

## Remaining limitations

- Local Gemma can still misinterpret requests or emit invalid protocol replies.
  Failed requests are reported without committing staged changes; review actual
  saved summaries and use undo. Unusually large single requests or long multi-tool workflows can still exceed
  the existing 8K context; normal subsequent conversation is bounded automatically.
- AI Library needs a real selected document source and a compatible embedding
  model/environment. Its worker/indexing/retrieval code is preserved and tested;
  no missing document path or embedding weights were invented or downloaded.
- Mews Segments and Activity Script require X11, optional automation packages,
  and screen-coordinate calibration. Native Wayland global automation is not
  available through the original tools.
- Hardware microphone selection, speech recognition on the user's voice,
  audible pronunciation and native file dialogs require direct user review.
  There is no continuous listening or wake word.
