"""JARVIS Rich UI hooks.

Two hooks are provided:

JarvisOrchestratorHook
    Intercepts ``spawn`` tool calls to print a visual dispatch panel, and
    summarises sub-agent completions after each iteration.

JarvisRichUIHook
    Prints structured panels around tool executions so multi-step operations
    are easy to follow at a glance.

Both hooks are *additive*: they only print extra Rich panels; they never
modify message content or interfere with the streaming output from
``StreamRenderer``.
"""

from __future__ import annotations

import json
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from nanobot.agent.hook import AgentHook, AgentHookContext


# ---------------------------------------------------------------------------
# Orchestrator hook — sub-agent dispatch / result logging
# ---------------------------------------------------------------------------


class JarvisOrchestratorHook(AgentHook):
    """Logs sub-agent dispatch and result summaries via Rich panels."""

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        spawn_calls = [tc for tc in context.tool_calls if tc.name == "spawn"]
        if not spawn_calls:
            return

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column("Agent", style="bold yellow", no_wrap=True)
        table.add_column("Task", style="white")

        for tc in spawn_calls:
            args: dict[str, Any] = {}
            if isinstance(tc.arguments, dict):
                args = tc.arguments
            elif isinstance(tc.arguments, str):
                try:
                    args = json.loads(tc.arguments)
                except Exception:
                    args = {}

            label = args.get("label") or "SubAgent"
            task = args.get("task", "")
            # Truncate long tasks for display
            preview = task[:80] + ("…" if len(task) > 80 else "")
            table.add_row(f"[{label}]", preview)

        self._console.print(
            Panel(
                table,
                title="[bold cyan]JARVIS — Dispatching[/bold cyan]",
                border_style="cyan",
                expand=False,
            )
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        # Show sub-agent results that came back this iteration
        spawn_results = []
        for result, tc in zip(context.tool_results, context.tool_calls):
            if tc.name == "spawn":
                args: dict[str, Any] = {}
                if isinstance(tc.arguments, dict):
                    args = tc.arguments
                elif isinstance(tc.arguments, str):
                    try:
                        args = json.loads(tc.arguments)
                    except Exception:
                        args = {}
                label = args.get("label") or "SubAgent"
                spawn_results.append((label, str(result)[:120]))

        if spawn_results:
            table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
            table.add_column("Agent", style="bold green", no_wrap=True)
            table.add_column("Status", style="dim")
            for label, preview in spawn_results:
                table.add_row(f"[{label}]", preview)
            self._console.print(
                Panel(
                    table,
                    title="[bold green]JARVIS — Sub-Agent Results[/bold green]",
                    border_style="green",
                    expand=False,
                )
            )


# ---------------------------------------------------------------------------
# Rich UI hook — tool call panels
# ---------------------------------------------------------------------------


# Tools that are too noisy to display individually
_QUIET_TOOLS = {"message"}


class JarvisRichUIHook(AgentHook):
    """Renders a compact tool-execution panel before each batch of tool calls.

    Does NOT enable streaming (``wants_streaming`` returns False) so it does
    not interfere with the CLI's ``StreamRenderer``.
    """

    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def wants_streaming(self) -> bool:
        return False

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        visible = [tc for tc in context.tool_calls if tc.name not in _QUIET_TOOLS]
        if not visible:
            return

        table = Table(box=box.SIMPLE, show_header=False, padding=(0, 1))
        table.add_column("Tool", style="bold magenta", no_wrap=True)
        table.add_column("Params", style="dim")

        for tc in visible:
            args: dict[str, Any] = {}
            if isinstance(tc.arguments, dict):
                args = tc.arguments
            elif isinstance(tc.arguments, str):
                try:
                    args = json.loads(tc.arguments)
                except Exception:
                    args = {}

            # Show the most meaningful param (first required key or longest value)
            param_preview = _format_args_preview(tc.name, args)
            table.add_row(tc.name, param_preview)

        self._console.print(
            Panel(
                table,
                title="[bold magenta]Tools[/bold magenta]",
                border_style="magenta",
                expand=False,
            )
        )

    async def after_iteration(self, context: AgentHookContext) -> None:
        usage = context.usage or {}
        if usage.get("completion_tokens"):
            tokens_in = usage.get("prompt_tokens", 0)
            tokens_out = usage.get("completion_tokens", 0)
            self._console.print(
                f"[dim]  ↳ tokens: {tokens_in} in / {tokens_out} out[/dim]"
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_args_preview(tool_name: str, args: dict[str, Any]) -> str:
    """Return a short human-readable preview of tool arguments."""
    if not args:
        return ""

    # Tool-specific priorities
    priority_keys: dict[str, list[str]] = {
        "read_file": ["path"],
        "write_file": ["path"],
        "edit_file": ["path"],
        "list_dir": ["path"],
        "exec": ["command"],
        "web_search": ["query"],
        "web_fetch": ["url"],
        "global_search": ["query", "path"],
        "take_screenshot": ["output_path"],
        "clipboard": ["action", "content"],
        "spawn": ["label", "task"],
        "cron": ["expression", "task"],
    }

    keys = priority_keys.get(tool_name, [])
    parts = []
    for k in keys:
        if k in args:
            val = str(args[k])
            parts.append(f"{k}={val[:40]}{'…' if len(val) > 40 else ''}")
    if parts:
        return "  ".join(parts)

    # Fallback: first item
    first_key = next(iter(args))
    val = str(args[first_key])
    return f"{first_key}={val[:60]}{'…' if len(val) > 60 else ''}"
