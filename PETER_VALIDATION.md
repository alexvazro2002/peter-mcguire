# Peter (McGuire) — verification record

## 16 October: compound task wording and complete notes

The final source passes **1,041 tests in 31.85 seconds** with the installed Python
environment and loopback fixtures enabled.

The original cooking/Recipes request failed after approximately 63 seconds: Qwen
returned three responses across about 24 seconds, then the fallback spent its
30-second response budget processing the larger prompt. Nothing was saved. The
completion parser had missed the unquoted note title and `include` payload, and it
did not distinguish a clock inside task wording from the requested schedule.

The user's clarification establishes that 21:00 is the schedule and “finish before
20:22” belongs to the task wording. A replay of the exact saved request in temporary
storage now completed in **0.0081 seconds**, with **zero model calls**, a task at
21:00 retaining that wording, and one Recipes note containing onion, garlic powder,
paprika, black pepper and salt. Both actions were saved in one undoable transaction.
No real test task or note was added to the user's workspace during validation.

Regression coverage includes exact schedule/title separation, all ingredients and
qualifiers, no split garlic/powder entries, no duplicated note section, idempotent
retry, joint undo, cancellation, preserving existing recipes, and deferring extra
unrecognised actions without partial writes. The note parser accepts unquoted and
quoted multiword names, `include`/`including`, and literal quoted action wording.

## 15 October: consistent voice, summaries and bounded action planning

The final source passes **991 tests in 31.62 seconds** with the installed Python
3.11 environment and local HTTP fixtures enabled. The exact stalled request with
“And Peter” and `9.13 pm`, replayed against the actual conversation in temporary
storage, created one verified linked task at `21:13` in **0.2058 seconds** with no
model calls or spoken waiting message. The original stalled run had sent 41 prior
messages to the planner, retried with Gemma, and continued until the app was closed
after more than five minutes. The direct parser and bounded model attempts address
those separate causes; failed staged work still cannot be committed.

Actual English and Spanish synthesis now uses the same `bm_george` speaker 26,
1.15 speed and +0.8 semitone processing. Only the phonemizer language changes.
Both samples transcribed correctly; approximate voiced median pitch was 152.9 Hz
for English and 151.9 Hz for Spanish. This checks settings, intelligibility and
rough pitch consistency, not a guarantee of identical perception across languages.

The actual cached three-email English summary request completed in **31.21 seconds**
with two model calls and three verified paraphrases, rather than Spanish excerpts.
Each message's body and readable attachment evidence contributes separately. The
check covers factual support and requested language; relative dates are anchored
to the original message date. Unknown attachments remain explicit. Detailed future
questions retrieve more evidence instead of treating the brief gist as complete.

Model planning now has a 45-second/eight-call budget per attempt and at most a
30-second HTTP response wait. The fallback gets one fresh bounded attempt and
releases only its own temporary model load before delivering an answer or error.
This prevents its idle model from competing with normal Peter for GPU memory.
Ownership and cancellation/rollback/resource-cleanup regressions are covered.

The desktop starts maximised with its normal frame. Peter uses a professionally
warm, lightly playful manner; routine greetings and confirmations no longer append
“sir”, and quick commands do not receive waiting preambles. Quoted source text is
preserved. Long reviews and work still running after seven seconds receive a
brief professional progress acknowledgement.

## 15 October: natural speech and automatic action recovery

The updated source passes **896 tests in 31.24 seconds** with the installed Python
3.11 environment, including actual loopback HTTP fixtures. Coverage includes
untouched date/time captions with voice-only normalization, English/Spanish
language detection, native Spanish speaker selection, queued progress speech,
cancellation, verified email reply tasks, duplicate prevention, undo, and safe
automatic retries before commit. The real missed email reply task was restored
for 15 October at 20:00 with its verified Gmail link and an undoable operation.

Actual local Kokoro audio was generated English → Spanish → English using
bm_george / em_alex / bm_george at 1.15× speed. Local transcription read back all
three synthetic samples correctly; no test audio was played through host speakers.
The installed Spanish dictionaries and phonemizer were used without downloading
another speech model. A deterministic local language detector uses bundled English
and Spanish profiles; no email content leaves the machine for speech synthesis.

A live recovery test deliberately used a primary model that only claimed success.
Peter automatically selected the already installed LM Studio Gemma 4 E4B model,
saved the exact synthetic note contents in temporary storage, and committed once.
The configured normal Qwen model remained unchanged. That cold retry took 57.503
seconds; a warm three-round note write took 52.442 seconds, so fallback remains
slower than ordinary answers. Both acknowledge progress before doing model work.
Clear current-email reply tasks bypass model planning and its retry delay.

These are measured checks, not a guarantee about every pronunciation, language
fragment or abstract request. Source backup excludes real email, calendar, notes,
conversation, credentials and runtime/cache files.

Checked 13 October 2026 on this Fedora installation. This record separates actual
local API and installation checks from tests using synthetic services and data.

## Responsiveness update

Peter now uses npm thinking-orbs 0.3.1 through its actual exported canvas engine.
Eight dark/light animations are rendered to lossless native-client WebP assets
with 144 frames each. All frames retain clear alpha borders and the first/last frames
match exactly. The loop compositor transform is reset before blending, fixing
clipping at the transition. Standby uses slow breathing, listening a gentle
waveform, talking a faster waveform, and thinking orbiting dots. These play without Python
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
Flet 0.28.3. The final NexusDesk test suite passes: **301 tests in 16.10 seconds** in the installed Python 3.11 environment. Tests are restricted to `tests/`
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
checked. No browser error/warning messages were captured during visual checks. Native
state updates exposed a Container decoration-field collision in the orb wrapper;
it was corrected and a mounted-control serialization regression was added. All view/tool controls
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

## Internet and Gmail update — 13 October 2026

- Current clocks recognize city spelling variants, including “china shang hai”, and answer without loading the model. Tested in the actual Flet window, with the written user message and answer visible.
- Actual LM Studio calls used public weather, web search, and page-reading tools. Weather: 4.84s; search: 2.58s; page title: 2.25s. All returned source links. Public HTTP connections use validated public IPs, hostname-verified TLS, bounded responses and revalidated redirects.
- Read-only Gmail browser OAuth is implemented with a real loopback callback, state validation, PKCE, protected local tokens and account-bound state. Startup checks save a pending digest and advance the checkpoint after durable delivery. Recovery, batches, account changes, failures and cancellation have synthetic coverage.
- The Google client JSON and account consent have not been supplied, so actual Gmail mailbox access is not connected or live-verified. Peter provides setup steps and accurate connection status.
- Final source suite: 359 passed. No private workspace files, OAuth credentials or Gmail contents are included in source packaging.

## British voice and Home update — 13 October 2026

- Installed the pinned Kokoro British male stock voice George (speaker 26) locally, using VoiceStudio’s existing Sherpa runtime. The actual eSpeak identifier `en` selects English (Great Britain); `en-gb` was rejected by the installed native API and was corrected before selection.
- Lowered pitch by 1.5 semitones using RubberBand with tempo/formants preserved. The 7.277-second final sample was transcribed word for word by local Whisper in 1.201 seconds. Four synthesis threads improved generation from 7.111 to 6.179 seconds for the same sample.
- Actual LM Studio replies matched the composed professional prompt, with British spelling and brief replies (0.95–2.36seconds in the sample requests). The selected model and its fast settings remain in LM Studio.
- Removed the duplicate Peter sidebar entry. Verified new Home messages appear, fade after 20 idle seconds, return with Show history, and hide without deleting stored conversation. History source and undo actions remain available.
- Installed regression suite: 381 passed. The speech preview contains only a synthetic test sentence; no personal speech or microphone recording was made for validation.


## Daily routine and natural voice — 14 October 2026

- Full source suite: 467 tests passed in 20.39 seconds, including local OAuth loopback tests.
- Browser interface with isolated synthetic data: morning readiness greeting appeared; “Yes, please” produced the scheduled 09:30 calendar item and unfinished 11:00 task, with a calendar source link.
- Native installation started successfully. The new morning greeting was persisted by the connected native Workspace and VoiceStudio health returned `ok`; no latest-startup Python traceback was found.
- George now uses natural pitch 0.0 and pace 1.0. The real local greeting sample contains 2.971 seconds of audio; generation took 2.527 seconds plus 0.745 seconds model load. No pitch filter runs at the default setting.
- Gmail startup/search fetch metadata only. Sender prompts wait for the calendar speech and active-account checks; direct consent reads one bounded body. Queued headers and staged delivery survive crashes, while active consent is discarded on restart. Synthetic API tests covered cancellation, account changes, stale consent, failures, and checkpoint preservation.
- Real Gmail authentication remains deferred; no real inbox was accessed in this update. Existing calendar, conversation and personal memory files were unchanged by source installation.

## Sentence playback — 14 October 2026

- Full source suite: 481 passed in 21.65 seconds. Additional assistant checks after the explicit read-only instruction passed (48 tests).
- The real local British voice pipeline generated “Great choice, sir. You have three new messages. Do you want me to read them?” as three clips. First audio became ready at 2.057 seconds; simulated timed playback measured successive gaps of 0.302 and 0.302 seconds. The preview contains only synthetic example text.
- One British model instance serves each reply, with generation overlapping playback and at most three unfinished clips in flight. Unit tests covered exact ordering, 0.3-second pauses, lookahead limits, long-sentence splitting, decimal/abbreviation boundaries, simultaneous synthesis/player cancellation, late HTTP cleanup, failures, atomic WAV publication and sentence bubbles with final-part source/undo controls.
- The installed native app restarted with no latest-startup Python traceback and a healthy VoiceStudio API. Natural pitch and pace remain unchanged.
- The supplied Google client was moved into Peter's private `.runtime/gmail/client.json` with mode 0600. The OAuth request uses only `gmail.readonly`; token checks reject broader scopes and the Gmail API adapter implements only GET. Google currently rejects sign-in until the account is added as a project test user. No real inbox was read during this validation.

## Hands-free conversation and opening items · 14 October 2026

The installed desktop copy passed **576 tests**. Hands-free capture uses local
WebRTC speech detection, 300 ms of preroll, and a roughly 900 ms silence boundary.
Capture pauses during inference and speech, then resumes after playback. Isolated
real-pipe tests verified silence rejection, multiple turns, cancellation, toggle
off, microphone failure without retry loops, and no capture during playback.
A synthetic British voice sample was detected and transcribed by the installed
local Whisper service in 1.52 seconds; no microphone or private email was used
for this transcription probe. The default native microphone delivered PCM in a
brief capture check; its audio was immediately discarded.

Natural opening requests resolve actual files or Gmail links, retain the account
reference, and report system-opener failures. Duplicate matches require a choice;
the next turn can select “the second one”. Email opening offers require an
explicit affirmative reply. Tests verified account changes, forged links,
filenames containing shell characters, document formats, and stale offers.
Gmail's API access remains read-only. No email send capability was introduced.

A synthetic browser preview at 1000 × 680 verified that the smaller translucent
previous message sits above the current caption, clicking it opens history, and
turning hands-free off restores Speak to Peter. Live captions still fade after
20 idle seconds; waiting for speech does not keep them visible indefinitely.

## Voice repair and inference stability · 14 October 2026

Both voice modes now capture raw PCM and build the transcription WAV in Python.
“Listening” is shown only after samples arrive, preventing empty recordings when
PipeWire takes time to start. Finish during startup waits for a minimum audio
buffer; mode changes cancel the previous capture. Missing or stalled microphone
audio produces a clear input-device error. Hands-free retries transient failures
with a delay and stops after three failures rather than looping indefinitely.

The Yeti Orb was confirmed as the user's microphone. The repaired native capture
returned 16,384 valid frames in 2.25 seconds during a brief discarded audio check.
Real local Whisper transcribed synthetic Gmail and Glasgow weather commands in
both modes, and all four transcripts reached the actual read-only Gmail or live
weather service successfully (1.45–1.93 seconds). No email bodies are in the
validation report. Ordinary dictated “e mails”, “G mail”, Peter prefixes and
polite commas are now recognised. Simple weather queries call the live service
directly; a missing city requests clarification.

The Gmail/weather error also reproduced a local Qwen inference-engine crash.
Loading the same installed LM Studio Qwen model with 80% GPU placement and one
parallel prediction left graphics-memory headroom; repeated multi-step weather
and calendar requests then succeeded. Unsupported-option retries now require an
explicit error identifying that option; model crashes and context overflow retain
their actual error instead of issuing several misleading retries. The chosen
model, 8,192-token context, voice pitch and speaking pace remain configured.

Full regression: **593 tests passed** in the source validation environment.

### Persistent capture and playback captions

The microphone remains open across hands-free turns. A separate local capture
thread discards samples during inference, transcription and playback rather than
closing and reopening the audio device. Turning hands-free off or closing Peter
stops the capture process. Controlled real-pipe tests verified multiple turns
with one microphone process and no transcription during replies.

The host's audio journal also reported ALSA USB allocation failures, affecting
both the Yeti input and Razer output. The local audio configuration now uses the
Yeti's Mono Input profile and Razer stereo output without its unused microphone
interface. A reversible local WirePlumber rule uses 48 kHz, 16-bit Razer playback
to reduce shared USB bandwidth. After reloading the session manager, the physical
Yeti remained armed through eight listening/reply gates with one capture process
and no audio errors. No microphone audio was saved or transcribed in that check.

Spoken replies no longer rotate through their text while speech is generating.
They wait for the first player process to start, then show that spoken sentence;
the next caption arrives when its audio starts. Sentence synthesis still runs
ahead during playback. Saved history retains the full response immediately, and
muted replies remain ordinary text. Playback failure reveals the saved text.

Cancellation invalidates queued voice work and transcript events before a new
request begins. Publication and microphone gates share the same lock, preventing
a cancelled utterance from starting later or leaving hands-free paused.
Regression checks cover cancellation after dequeue, during transcript
publication, and between microphone readiness and queue insertion.

Final regression: **599 tests passed**.

## Natural conversation and grounded follow-ups · 14 October 2026

Peter now uses conversational British English, contractions and plain spoken
sentences, with a 512-token routine reply budget. Generic greetings no longer ask
for machine status. Factual email readings explain the message without unsolicited
salary comparisons or invented job details; assessments remain available when asked.
Links and source cards remain in the interface rather than the spoken prose.

Conversation context considers up to 40 recent messages, preserving complete
turns within the available budget. Restart greetings and app errors are excluded.
App-recorded source and action metadata accompany this context, while older
assistant prose is explicitly not evidence. The existing LM Studio Qwen 9B model
was reloaded at 16,384 context, 80% GPU placement, one prediction and thinking off.
The model remained healthy through the actual local dialogue checks.

Email follow-ups retain verified references across errors and understand detail
and salary questions. Sender/time refinements use the user's prior requests.
Straightforward searches resolve local dates and clock times before inference;
Gmail date operators are converted to epoch seconds to avoid Pacific-midnight
interpretation. Returned ranges accompany the results. See Google's
[Gmail search documentation](https://developers.google.com/workspace/gmail/api/guides/filtering).
Unreplied mail is not silently substituted with unread mail.

Unsupported search/read/action claims, fabricated Gmail links, invented subjects
and counts exceeding returned results are checked before display and playback.
If generation repeatedly fails these checks, verified search headers provide an
honest fallback. Plural opening uses real source references or repeats the user's
search; previous prose cannot supply message IDs. Browser-closing requests state
the current capability honestly. Gmail permissions remain read-only.

Actual-model verification used synthetic mail and mocked opening, never private
email bodies or real browser actions. Social replies took 3.85–6.14 seconds.
Routine date searches and refinements returned the actual synthetic messages;
the final factual detail and salary replies took 4.15 and 2.22 seconds. Both
messages opened in order in the controlled opener check. A separate 26-message
conversation recalled the original room and guest correctly in 7.44 seconds.

Final installed regression: **634 tests passed**.
