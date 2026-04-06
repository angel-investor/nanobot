"""OpenAI Whisper transcription provider (fallback when GROQ_API_KEY is absent)."""

from __future__ import annotations

import os
from pathlib import Path


class OpenAITranscriptionProvider:
    """STT via OpenAI Whisper API."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

    async def transcribe(self, file_path: str | Path) -> str:
        from openai import AsyncOpenAI
        from jarvis.voice.utils import is_hallucination
        client = AsyncOpenAI(api_key=self.api_key)
        path = Path(file_path)
        with open(path, "rb") as f:
            result = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="zh",   # 中文优先，识别英文也正常
            )
        text = result.text.strip()
        if is_hallucination(text):
            return ""
        return text
