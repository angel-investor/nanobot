"""Text-to-speech backends for JARVIS."""

from __future__ import annotations

import asyncio
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path


TTS_CHAR_LIMIT = 500   # responses longer than this are not spoken


class TTSBackend(ABC):
    @abstractmethod
    async def speak(self, text: str) -> None: ...


class MacOSSayBackend(TTSBackend):
    """Zero-dependency TTS via macOS built-in `say` command."""

    def __init__(self, voice: str = "Ting-Ting", rate: int = 180) -> None:
        self._voice = voice  # "Ting-Ting" for Chinese, "Samantha" for English
        self._rate = rate

    async def speak(self, text: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "say", "-v", self._voice, "-r", str(self._rate), text,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()


class OpenAITTSBackend(TTSBackend):
    """TTS via OpenAI text-to-speech API; plays with `afplay` (macOS)."""

    def __init__(
        self,
        api_key: str | None = None,
        voice: str = "onyx",
        model: str = "tts-1",
    ) -> None:
        self._api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self._voice = voice
        self._model = model

    async def speak(self, text: str) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as e:
            raise ImportError("openai package required for OpenAI TTS") from e

        client = AsyncOpenAI(api_key=self._api_key)
        tmp = Path(tempfile.mktemp(suffix=".mp3", prefix="jarvis_tts_"))
        try:
            response = await client.audio.speech.create(
                model=self._model,
                voice=self._voice,  # type: ignore[arg-type]
                input=text,
            )
            response.stream_to_file(tmp)
            proc = await asyncio.create_subprocess_exec(
                "afplay", str(tmp),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate()
        finally:
            if tmp.exists():
                tmp.unlink()


def make_tts_backend(prefer_openai: bool = False) -> TTSBackend:
    """Return OpenAI TTS if requested and key is available, else macOS say."""
    if prefer_openai and os.environ.get("OPENAI_API_KEY"):
        return OpenAITTSBackend()
    return MacOSSayBackend()
