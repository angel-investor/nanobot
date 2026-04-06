"""VoiceController — Siri-style voice I/O for JARVIS.

Interaction states:
    IDLE      → small circle, waiting for wake word
    LISTENING → real-time waveform panel (à la Siri)
    THINKING  → spinner while LLM processes
    SPEAKING  → TTS playback with text display
"""

from __future__ import annotations

import asyncio
import threading
from collections import deque
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from jarvis.voice.recorder import record_until_silence
from jarvis.voice.tts import TTS_CHAR_LIMIT, TTSBackend, make_tts_backend
from jarvis.voice.utils import is_hallucination

# ---------------------------------------------------------------------------
# Waveform rendering helpers
# ---------------------------------------------------------------------------

_BAR_CHARS = " ▁▂▃▄▅▆▇█"
_WAVE_COLS = 24  # number of amplitude columns in the waveform display


def _rms_to_bar(rms: float) -> str:
    """Map an RMS value [0, 1] to a single bar character."""
    idx = min(int(rms * 80), len(_BAR_CHARS) - 1)
    return _BAR_CHARS[idx]


def _render_waveform(levels: deque[float], state_label: str) -> Panel:
    """Render a Siri-style waveform panel."""
    bars = "".join(_rms_to_bar(v) for v in levels)
    wave_line = Text(bars, style="bold cyan", justify="center")
    label = Text(f"  {state_label}", style="dim", justify="center")
    content = Text.assemble(wave_line, "\n", label)
    return Panel(content, border_style="cyan", padding=(0, 2), expand=False)


# ---------------------------------------------------------------------------
# STT factory
# ---------------------------------------------------------------------------

def _make_stt():
    """Return the best available STT provider based on configured API keys."""
    import os
    if os.environ.get("GROQ_API_KEY"):
        from nanobot.providers.transcription import GroqTranscriptionProvider
        return GroqTranscriptionProvider()
    if os.environ.get("OPENAI_API_KEY"):
        from jarvis.voice.stt_openai import OpenAITranscriptionProvider
        return OpenAITranscriptionProvider()
    raise RuntimeError(
        "No STT API key found. Set GROQ_API_KEY (recommended) or OPENAI_API_KEY."
    )


# ---------------------------------------------------------------------------
# VoiceController
# ---------------------------------------------------------------------------

class VoiceController:
    """High-level voice I/O facade with Siri-style UI.

    Usage::

        vc = VoiceController(console)
        text = await vc.listen()     # blocks until speech recognised
        await vc.speak(response)     # TTS playback
    """

    def __init__(
        self,
        console: Console,
        *,
        prefer_openai_tts: bool = False,
    ) -> None:
        self._console = console
        self._stt = _make_stt()
        self._tts: TTSBackend = make_tts_backend(prefer_openai=prefer_openai_tts)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def listen(self) -> str:
        """Wait for Enter, show waveform UI, transcribe, return text.

        Returns empty string when no speech was detected (caller loops back).
        """
        # ── IDLE state — wait for Enter ──────────────────────────────────
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: input("\n[按 Enter 开始说话，Ctrl+C 退出]"),
        )

        # ── Play activation chime ────────────────────────────────────────
        proc = await asyncio.create_subprocess_exec(
            "afplay", "/System/Library/Sounds/Ping.aiff",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()

        # ── LISTENING state — waveform Live display ──────────────────────
        levels: deque[float] = deque([0.0] * _WAVE_COLS, maxlen=_WAVE_COLS)
        level_lock = threading.Lock()
        recording_done = asyncio.Event()
        wav_result: list[Path | None] = [None]

        def _on_level(rms: float) -> None:
            with level_lock:
                levels.append(rms)

        async def _run_recording() -> None:
            path = await record_until_silence(on_level=_on_level)
            wav_result[0] = path
            recording_done.set()

        record_task = asyncio.create_task(_run_recording())

        # Animate waveform while recording
        with Live(
            _render_waveform(levels, "🎙  聆听中... (停顿即结束)"),
            console=self._console,
            refresh_per_second=20,
            transient=True,
        ) as live:
            while not recording_done.is_set():
                await asyncio.sleep(0.05)
                with level_lock:
                    snap = deque(levels, maxlen=_WAVE_COLS)
                live.update(_render_waveform(snap, "🎙  聆听中... (停顿即结束)"))

        await record_task

        wav_path = wav_result[0]
        if wav_path is None:
            self._console.print("[dim]  ○  未检测到语音，重新等待...[/dim]")
            return ""

        # ── THINKING state — transcribe ──────────────────────────────────
        try:
            with self._console.status("[cyan]  ◌  识别中...[/cyan]", spinner="dots"):
                text = await self._stt.transcribe(wav_path)
            text = text.strip()

            if not text or is_hallucination(text):
                self._console.print("[dim]  ○  未识别到语音，重新等待...[/dim]")
                return ""

            self._console.print(f"[bold cyan]You[/bold cyan] › {text}")
            return text

        except Exception as exc:
            self._console.print(f"[red]语音输入失败：{exc}[/red]")
            return ""
        finally:
            if wav_path and wav_path.exists():
                wav_path.unlink(missing_ok=True)

    async def speak(self, text: str) -> None:
        """Speak text via TTS; skip if it exceeds TTS_CHAR_LIMIT."""
        if not text:
            return
        if len(text) > TTS_CHAR_LIMIT:
            self._console.print(
                f"[dim]回复过长（{len(text)} 字），跳过朗读[/dim]"
            )
            return
        try:
            await self._tts.speak(text)
        except Exception as exc:
            self._console.print(f"[red]TTS 失败：{exc}[/red]")
