"""Wake word detection using openwakeword (local, offline).

Listens continuously in the background; resolves when "hey jarvis" is detected.
"""

from __future__ import annotations

import asyncio

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
CHUNK_SIZE = 1280        # 80 ms — required by openwakeword
WAKEWORD_THRESHOLD = 0.15


def _blocking_listen(stop_event: asyncio.Event, loop: asyncio.AbstractEventLoop) -> None:
    """Blocking mic loop; runs in a thread executor.

    Feeds 80 ms chunks to the openwakeword model and signals stop_event
    via the event loop when the wake word score exceeds the threshold.
    """
    from openwakeword.model import Model

    model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")

    def _signal():
        loop.call_soon_threadsafe(stop_event.set)

    with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32",
                        blocksize=CHUNK_SIZE) as stream:
        while not stop_event.is_set():
            chunk, _ = stream.read(CHUNK_SIZE)
            flat = chunk[:, 0].astype("float32")  # Ensure float32 [-1.0, 1.0]
            prediction = model.predict(flat)
            score = next(iter(prediction.values()), 0.0)
            if score >= WAKEWORD_THRESHOLD:
                _signal()
                return


async def wait_for_wakeword() -> None:
    """Async wrapper: returns when 'hey jarvis' is detected."""
    loop = asyncio.get_event_loop()
    stop_event = asyncio.Event()
    await loop.run_in_executor(None, _blocking_listen, stop_event, loop)
