"""Run this directly to tune the wake word threshold.

    python3 jarvis/voice/test_wakeword.py

Say "hey jarvis" repeatedly and watch the scores.
Ctrl+C to stop.
"""
import numpy as np
import sounddevice as sd
from openwakeword.model import Model

model = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
print("Listening... say 'hey jarvis' (Ctrl+C to stop)\n")

CHUNK = 1280
with sd.InputStream(samplerate=16000, channels=1, dtype="float32", blocksize=CHUNK) as stream:
    while True:
        chunk, _ = stream.read(CHUNK)
        pred = model.predict(chunk[:, 0].astype("float32"))
        score = next(iter(pred.values()), 0.0)
        bar = "█" * int(score * 40)
        print(f"\r  score={score:.3f}  {bar:<40}", end="", flush=True)
        if score >= 0.3:
            print(f"\n  *** DETECTED (score={score:.3f}) ***")

