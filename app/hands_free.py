"""Local utterance boundaries over 16 kHz mono PCM; no ambient audio storage."""
from collections import deque


class UtteranceDetector:
    frame_bytes = 960  # 30 ms of signed 16-bit PCM at 16 kHz.

    def __init__(self, *, vad=None, max_seconds=30, silence_seconds=.9):
        if vad is None:
            import webrtcvad
            vad = webrtcvad.Vad(2)
        self.vad = vad
        self.max_frames = max(167, int(max_seconds / .03))
        self.silence_frames = max(10, round(silence_seconds / .03))
        self.reset()

    def reset(self):
        self.preroll = deque(maxlen=10)
        self.onset = deque(maxlen=5)
        self.frames, self.voiced, self.silent = [], 0, 0
        self.active = False

    def feed(self, frame):
        if len(frame) != self.frame_bytes:
            raise ValueError('Speech frames must contain exactly 30 ms of PCM.')
        speech = self.vad.is_speech(frame, 16000)
        started = False
        if not self.active:
            self.preroll.append(frame)
            self.onset.append(speech)
            if sum(self.onset) >= 4:
                self.active, started = True, True
                self.frames = list(self.preroll)
                self.voiced = sum(self.onset)
            return started, None
        self.frames.append(frame)
        self.voiced += int(speech)
        self.silent = 0 if speech else self.silent + 1
        if self.silent >= self.silence_frames or len(self.frames) >= self.max_frames:
            # Short clicks and transient noise are discarded, including their
            # preroll. Retain a natural trailing pause for the recogniser.
            pcm = b''.join(self.frames) if self.voiced >= 8 else None
            self.reset()
            return False, pcm
        return False, None
