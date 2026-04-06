"""Microphone recording with VAD (Voice Activity Detection) and silence detection.

Requires the 'voice' optional extras:
    pip install -e ".[voice]"

Key improvement over the original:
- Speech onset detection: recording only starts after real voice is detected,
  preventing silent audio from being sent to Whisper (which causes hallucinations).
- on_level callback: exposes real-time RMS level for waveform UI.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections import deque
from pathlib import Path
from typing import Callable

try:
    import numpy as np
    import sounddevice as sd
    import soundfile as sf
except ImportError as _e:
    raise ImportError(
        "Voice dependencies not installed. Run: pip install -e '.[voice]'"
    ) from _e


SAMPLE_RATE = 16_000            # Hz — Whisper's native rate
CHANNELS = 1
FRAME_SIZE = 1600               # samples per frame (0.1 s)
SILENCE_THRESHOLD = 0.015       # RMS below this → silent
SPEECH_ONSET_THRESHOLD = 0.045  # RMS above this → real speech detected
SILENCE_DURATION_SEC = 1.2      # stop after this many consecutive silent seconds
MAX_WAIT_FOR_SPEECH_SEC = 8.0   # give up if no speech detected in this window
MAX_DURATION_SEC = 60.0         # hard cap on recording length
PRE_SPEECH_BUFFER_FRAMES = 5    # frames to keep before speech onset (avoids clipping)


def _blocking_record(
    *,
    sample_rate: int,
    silence_threshold: float,
    speech_onset_threshold: float,
    silence_duration_sec: float,
    max_wait_for_speech_sec: float,
    max_duration_sec: float,
    on_level: Callable[[float], None] | None,
) -> np.ndarray:
    """Blocking mic capture with VAD; runs in a thread executor.

    State machine:
        WAITING  — listening for speech onset (RMS > speech_onset_threshold)
        RECORDING — capturing; stops after silence_duration_sec of silence
        DONE
    """
    pre_speech_buf: deque[np.ndarray] = deque(maxlen=PRE_SPEECH_BUFFER_FRAMES)
    frames: list[np.ndarray] = []

    max_silent_frames = int(silence_duration_sec * sample_rate / FRAME_SIZE)
    max_wait_frames = int(max_wait_for_speech_sec * sample_rate / FRAME_SIZE)
    max_record_frames = int(max_duration_sec * sample_rate / FRAME_SIZE)

    state = "WAITING"
    silent_frames = 0
    wait_frames = 0
    done = False

    def _callback(indata: np.ndarray, frame_count: int, time_info, status) -> None:
        nonlocal state, silent_frames, wait_frames, done

        chunk = indata.copy()
        rms = float(np.sqrt(np.mean(chunk ** 2)))

        if on_level is not None:
            on_level(rms)

        if state == "WAITING":
            pre_speech_buf.append(chunk)
            wait_frames += 1
            if rms >= speech_onset_threshold:
                # Speech detected — flush pre-speech buffer into frames
                state = "RECORDING"
                frames.extend(pre_speech_buf)
                silent_frames = 0
            elif wait_frames >= max_wait_frames:
                # No speech in the timeout window
                done = True
                raise sd.CallbackStop()

        elif state == "RECORDING":
            frames.append(chunk)
            if rms < silence_threshold:
                silent_frames += 1
            else:
                silent_frames = 0
            if silent_frames >= max_silent_frames or len(frames) >= max_record_frames:
                done = True
                raise sd.CallbackStop()

    with sd.InputStream(
        samplerate=sample_rate,
        channels=CHANNELS,
        dtype="float32",
        blocksize=FRAME_SIZE,
        callback=_callback,
    ):
        while not done:
            sd.sleep(50)

    if not frames:
        return np.zeros((0, CHANNELS), dtype="float32")
    return np.concatenate(frames, axis=0)


async def record_until_silence(
    *,
    sample_rate: int = SAMPLE_RATE,
    silence_threshold: float = SILENCE_THRESHOLD,
    speech_onset_threshold: float = SPEECH_ONSET_THRESHOLD,
    silence_duration_sec: float = SILENCE_DURATION_SEC,
    max_wait_for_speech_sec: float = MAX_WAIT_FOR_SPEECH_SEC,
    max_duration_sec: float = MAX_DURATION_SEC,
    on_level: Callable[[float], None] | None = None,
    output_path: Path | None = None,
) -> Path | None:
    """Record from the default mic with VAD, stop on silence.

    Returns:
        Path to WAV file if speech was detected, None if no speech within timeout.

    The caller is responsible for deleting the file after use.
    """
    if output_path is None:
        output_path = Path(tempfile.mktemp(suffix=".wav", prefix="jarvis_voice_"))

    loop = asyncio.get_event_loop()
    audio = await loop.run_in_executor(
        None,
        lambda: _blocking_record(
            sample_rate=sample_rate,
            silence_threshold=silence_threshold,
            speech_onset_threshold=speech_onset_threshold,
            silence_duration_sec=silence_duration_sec,
            max_wait_for_speech_sec=max_wait_for_speech_sec,
            max_duration_sec=max_duration_sec,
            on_level=on_level,
        ),
    )

    if audio.shape[0] == 0:
        return None  # No speech detected

    await loop.run_in_executor(
        None, lambda: sf.write(str(output_path), audio, sample_rate)
    )
    return output_path
