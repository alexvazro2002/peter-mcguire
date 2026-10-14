# Peter’s voice and Home conversation

Peter now speaks with the British male Kokoro stock voice **George**, at a measured pace. The speech path uses the installed Sherpa ONNX runtime in VoiceStudio’s isolated Python environment. It synthesizes locally from revision-pinned model files; no actor recording or voice clone is used.

The default speaking pace is `1.0`, with George's natural pitch and no audio pitch processing. This keeps the British male voice clear and avoids the artificial depth of the previous setting. Settings → Peter · Local voice exposes pace and pitch for optional adjustments; FFmpeg with the `rubberband` filter is needed only for a nonzero pitch adjustment. VoiceStudio continues to provide local microphone transcription.

Speech now uses a sentence queue. The British voice model loads once per reply, publishes the first sentence as soon as it is ready, then synthesizes later sentences while the earlier clip plays. At most three unfinished clips are kept ahead. Playback remains sequential, with a cancellable 0.3-second pause between clips and conservative trimming of silent audio padding. Unusually long sentences are divided into bounded phrases. Stop cancels both synthesis and playback; temporary text/audio are erased after the workers exit. Plain one-line replies also appear as individual sentence bubbles, while lists and multiline email bodies retain their layout.

Peter’s instructions require British English, direct short answers, a calm formal manner and restrained dry wit. They discourage excitement, filler, flattery and repetitive apologies. The startup greeting follows the local time: “Good morning/afternoon/evening, sir. Ready for today?” A positive reply starts the calendar briefing. The selected reasoning model remains the existing LM Studio model.

The duplicated Peter sidebar item is removed. Home shows the current typed or spoken conversation as it arrives, then fades it over 300 milliseconds after twenty idle seconds. Listening, model work and speech keep the current messages visible and restart the idle timer. **Show history** displays the saved conversation; **Hide** closes it until a new message arrives. Hiding messages never deletes conversation data. Source buttons and undo remain available in Home history.

## Installation

`install-voice.sh` prepares the existing local voice service and invokes `scripts/install_british_voice.py`. That explicit installer downloads about 153 MiB from the Sherpa maintainer’s public Kokoro model repository, pinned to revision `2a360693d79b88b49b88e29aec2b53577f41f206`. It verifies the model and voice-vector SHA256 digests. Startup itself never downloads speech models. Runtime weights and audio remain outside source packages and Git.

The [official speaker mapping](https://k2-fsa.github.io/sherpa/onnx/tts/all/Chinese-English/kokoro-multi-lang-v1_0.html) identifies George as British male speaker 26. [FFmpeg’s filter documentation](https://ffmpeg.org/ffmpeg-filters.html#rubberband) describes separate pitch and tempo adjustment.
