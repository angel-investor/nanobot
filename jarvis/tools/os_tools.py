"""JARVIS OS tool extensions.

Three additional tools registered in jarvis_main.py:
  - GlobalSearchTool  : keyword search across local project files
  - ScreenshotTool    : capture screen, return file path (multimodal-ready)
  - ClipboardTool     : read / write system clipboard
"""

from __future__ import annotations

import asyncio
import os
import platform
import sys
import tempfile
import time
from typing import Any

from nanobot.agent.tools.base import Tool


# ---------------------------------------------------------------------------
# 1. Global Search
# ---------------------------------------------------------------------------


class GlobalSearchTool(Tool):
    """Search local project files for a keyword using grep.

    Returns up to ``max_results`` matching lines in ``path:line:content`` format.
    Safe to run concurrently (read-only).
    """

    def __init__(self, default_max_results: int = 50) -> None:
        self._default_max = default_max_results

    @property
    def name(self) -> str:
        return "global_search"

    @property
    def description(self) -> str:
        return (
            "Search local files for a keyword or regex pattern. "
            "Returns matching lines with file path and line number. "
            "Use this to find code, documentation, or any text across the project."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword or regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory or file path to search in. "
                        "Defaults to the current working directory."
                    ),
                },
                "case_sensitive": {
                    "type": "boolean",
                    "description": "Whether the search is case-sensitive. Default: false.",
                },
                "max_results": {
                    "type": "integer",
                    "description": f"Maximum number of matching lines to return. Default: {self._default_max}.",
                },
            },
            "required": ["query"],
        }

    @property
    def read_only(self) -> bool:
        return True

    async def execute(
        self,
        query: str,
        path: str = ".",
        case_sensitive: bool = False,
        max_results: int | None = None,
        **kwargs: Any,
    ) -> str:
        limit = max_results if max_results is not None else self._default_max

        grep_flags = ["-rn", "--include=*"]
        if not case_sensitive:
            grep_flags.append("-i")

        cmd = ["grep"] + grep_flags + [query, path]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return "Error: grep not found on this system."

        output = stdout.decode(errors="replace")
        lines = [ln for ln in output.splitlines() if ln.strip()]

        if not lines:
            return f"No matches found for '{query}' in '{path}'."

        truncated = len(lines) > limit
        result_lines = lines[:limit]
        result = "\n".join(result_lines)
        if truncated:
            result += f"\n\n[Results truncated at {limit} lines. Use a more specific query or narrow the path.]"
        return result


# ---------------------------------------------------------------------------
# 2. Screenshot
# ---------------------------------------------------------------------------


class ScreenshotTool(Tool):
    """Capture the current screen and save it to a temporary file.

    Returns the absolute file path of the saved image.
    The path can be passed to the ``message`` tool's ``media`` parameter
    to send the image to a multimodal model.

    Supports macOS (screencapture) and Linux with a display (scrot / gnome-screenshot).
    """

    @property
    def name(self) -> str:
        return "take_screenshot"

    @property
    def description(self) -> str:
        return (
            "Capture a screenshot of the current screen and save it to a file. "
            "Returns the file path of the saved PNG image. "
            "The path can then be passed to the message tool's media parameter "
            "to share the image with a multimodal model."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "output_path": {
                    "type": "string",
                    "description": (
                        "Optional file path to save the screenshot. "
                        "If omitted, a temporary file is created automatically."
                    ),
                },
            },
            "required": [],
        }

    async def execute(self, output_path: str | None = None, **kwargs: Any) -> str:
        if output_path is None:
            ts = int(time.time())
            output_path = os.path.join(tempfile.gettempdir(), f"jarvis_screenshot_{ts}.png")

        system = platform.system()

        if system == "Darwin":
            cmd = ["screencapture", "-x", output_path]
        elif system == "Linux":
            if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
                return (
                    "Screenshot failed: no graphical display detected. "
                    "Set DISPLAY or WAYLAND_DISPLAY before running JARVIS in a GUI session."
                )
            # Try scrot first, fall back to gnome-screenshot
            if await _command_exists("scrot"):
                cmd = ["scrot", output_path]
            elif await _command_exists("gnome-screenshot"):
                cmd = ["gnome-screenshot", "-f", output_path]
            else:
                return (
                    "Screenshot failed: no supported screenshot tool found. "
                    "Install scrot (`apt install scrot`) or gnome-screenshot."
                )
        else:
            return f"Screenshot not supported on {system}."

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
        except FileNotFoundError as exc:
            return f"Screenshot failed: command not found — {exc}"

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            # macOS Sequoia+ may return non-zero when permission dialog is shown
            if system == "Darwin" and os.path.exists(output_path):
                return output_path
            return f"Screenshot failed (exit {proc.returncode}): {err}"

        if not os.path.exists(output_path):
            return "Screenshot command succeeded but no file was created."

        return output_path


# ---------------------------------------------------------------------------
# 3. Clipboard
# ---------------------------------------------------------------------------


class ClipboardTool(Tool):
    """Read from or write to the system clipboard.

    Uses platform-native commands (pbpaste/pbcopy on macOS, xclip/xsel on Linux)
    so no extra Python dependencies are required.
    """

    @property
    def name(self) -> str:
        return "clipboard"

    @property
    def description(self) -> str:
        return (
            "Read from or write to the system clipboard. "
            "action='read' returns the current clipboard contents. "
            "action='write' sets the clipboard to the given content string."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "write"],
                    "description": "'read' to get clipboard contents, 'write' to set them.",
                },
                "content": {
                    "type": "string",
                    "description": "Text to write to the clipboard. Required when action='write'.",
                },
            },
            "required": ["action"],
        }

    async def execute(self, action: str, content: str = "", **kwargs: Any) -> str:
        system = platform.system()

        if action == "read":
            return await self._read(system)
        elif action == "write":
            return await self._write(system, content)
        else:
            return f"Unknown action '{action}'. Use 'read' or 'write'."

    async def _read(self, system: str) -> str:
        if system == "Darwin":
            cmd = ["pbpaste"]
        elif system == "Linux":
            if await _command_exists("xclip"):
                cmd = ["xclip", "-selection", "clipboard", "-o"]
            elif await _command_exists("xsel"):
                cmd = ["xsel", "--clipboard", "--output"]
            else:
                return (
                    "Clipboard read failed: no clipboard tool found. "
                    "Install xclip (`apt install xclip`) on Linux."
                )
        else:
            return f"Clipboard not supported on {system}."

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
        except FileNotFoundError as exc:
            return f"Clipboard read failed: {exc}"

        return stdout.decode(errors="replace")

    async def _write(self, system: str, content: str) -> str:
        if system == "Darwin":
            cmd = ["pbcopy"]
        elif system == "Linux":
            if await _command_exists("xclip"):
                cmd = ["xclip", "-selection", "clipboard"]
            elif await _command_exists("xsel"):
                cmd = ["xsel", "--clipboard", "--input"]
            else:
                return (
                    "Clipboard write failed: no clipboard tool found. "
                    "Install xclip (`apt install xclip`) on Linux."
                )
        else:
            return f"Clipboard not supported on {system}."

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate(input=content.encode())
        except FileNotFoundError as exc:
            return f"Clipboard write failed: {exc}"

        if proc.returncode != 0:
            err = stderr.decode(errors="replace").strip()
            return f"Clipboard write failed (exit {proc.returncode}): {err}"

        preview = content[:60] + ("…" if len(content) > 60 else "")
        return f"Clipboard updated: \"{preview}\""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _command_exists(cmd: str) -> bool:
    """Return True if ``cmd`` is available on PATH."""
    which = "where" if platform.system() == "Windows" else "which"
    try:
        proc = await asyncio.create_subprocess_exec(
            which, cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
