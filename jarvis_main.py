#!/usr/bin/env python3
"""JARVIS — Just A Rather Very Intelligent System.

A personalised AI butler built on nanobot.

Usage:
    python jarvis_main.py              # interactive mode
    python jarvis_main.py -m "..."     # single message
    python jarvis_main.py --logs       # show debug logs
"""

from __future__ import annotations

import asyncio
import signal
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

# ---------------------------------------------------------------------------
# JARVIS banner
# ---------------------------------------------------------------------------

JARVIS_BANNER = """[bold cyan]     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗[/bold cyan]
[bold cyan]     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝[/bold cyan]
[bold cyan]     ██║███████║██████╔╝██║   ██║██║███████╗[/bold cyan]
[bold cyan]██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║[/bold cyan]
[bold cyan]╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║[/bold cyan]
[bold cyan] ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝[/bold cyan]
[dim]Just A Rather Very Intelligent System[/dim]
[dim]Powered by nanobot × DeepSeek[/dim]"""

console = Console()


def print_banner() -> None:
    console.print()
    console.print(Panel(JARVIS_BANNER, border_style="cyan", expand=False, padding=(0, 2)))
    console.print()


# ---------------------------------------------------------------------------
# Provider / config helpers (mirror nanobot.cli.commands internals)
# ---------------------------------------------------------------------------


def _build_provider(config):
    """Create an LLM provider from a loaded Config object."""
    from nanobot.providers.base import GenerationSettings
    from nanobot.providers.registry import find_by_name

    model = config.agents.defaults.model
    provider_name = config.get_provider_name(model)
    p = config.get_provider(model)
    spec = find_by_name(provider_name) if provider_name else None
    backend = spec.backend if spec else "openai_compat"

    if backend == "anthropic":
        from nanobot.providers.anthropic_provider import AnthropicProvider
        provider = AnthropicProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
        )
    else:
        from nanobot.providers.openai_compat_provider import OpenAICompatProvider
        provider = OpenAICompatProvider(
            api_key=p.api_key if p else None,
            api_base=config.get_api_base(model),
            default_model=model,
            extra_headers=p.extra_headers if p else None,
            spec=spec,
        )

    defaults = config.agents.defaults
    provider.generation = GenerationSettings(
        temperature=defaults.temperature,
        max_tokens=defaults.max_tokens,
        reasoning_effort=defaults.reasoning_effort,
    )
    return provider


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def _run(
    message: str | None,
    session_id: str,
    markdown: bool,
    logs: bool,
    voice: bool = False,
    openai_tts: bool = False,
) -> None:
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.bus.events import InboundMessage
    from nanobot.cli.stream import StreamRenderer, ThinkingSpinner
    from nanobot.config.loader import load_config
    from nanobot.cron.service import CronService
    from nanobot.utils.helpers import sync_workspace_templates

    from jarvis.hooks.rich_ui import JarvisOrchestratorHook, JarvisRichUIHook
    from jarvis.tools.os_tools import GlobalSearchTool, ScreenshotTool, ClipboardTool

    if logs:
        logger.enable("nanobot")
    else:
        logger.disable("nanobot")

    # Load config and sync workspace templates
    config = load_config()
    sync_workspace_templates(config.workspace_path)

    provider = _build_provider(config)
    bus = MessageBus()

    defaults = config.agents.defaults

    # Cron store
    cron_store_path = config.workspace_path / "cron" / "jobs.json"
    cron = CronService(cron_store_path)

    # JARVIS hooks
    jarvis_hooks = [
        JarvisRichUIHook(console),
        JarvisOrchestratorHook(console),
    ]

    # Build AgentLoop
    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=config.workspace_path,
        model=defaults.model,
        max_iterations=defaults.max_tool_iterations,
        context_window_tokens=defaults.context_window_tokens,
        context_block_limit=defaults.context_block_limit,
        max_tool_result_chars=defaults.max_tool_result_chars,
        provider_retry_mode=defaults.provider_retry_mode,
        web_search_config=config.tools.web.search,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=cron,
        restrict_to_workspace=config.tools.restrict_to_workspace,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        timezone=defaults.timezone,
        hooks=jarvis_hooks,
    )

    # Register JARVIS OS tools
    agent_loop.tools.register(GlobalSearchTool())
    agent_loop.tools.register(ScreenshotTool())
    agent_loop.tools.register(ClipboardTool())

    # ------------------------------------------------------------------
    # Single-message mode
    # ------------------------------------------------------------------
    if message:
        renderer = StreamRenderer(render_markdown=markdown)
        response = await agent_loop.process_direct(
            message, session_id,
            on_stream=renderer.on_delta,
            on_stream_end=renderer.on_end,
        )
        if not renderer.streamed:
            await renderer.close()
            if response and response.content:
                console.print(response.content)
        await agent_loop.close_mcp()
        return

    # ------------------------------------------------------------------
    # Interactive mode
    # ------------------------------------------------------------------
    voice_controller = None
    if voice:
        from jarvis.voice import VoiceController
        voice_controller = VoiceController(console, prefer_openai_tts=openai_tts)
        console.print("[dim]Voice mode active. Press [bold]Enter[/bold] to speak.[/dim]\n")

    print_banner()
    console.print(
        "[dim]Type [bold]exit[/bold] or press [bold]Ctrl+C[/bold] to quit. "
        "Ready, sir.[/dim]\n"
    )

    if ":" in session_id:
        cli_channel, cli_chat_id = session_id.split(":", 1)
    else:
        cli_channel, cli_chat_id = "cli", session_id

    def _handle_signal(signum, frame):
        console.print("\n[cyan]JARVIS signing off. Goodbye, sir.[/cyan]")
        sys.exit(0)

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, _handle_signal)
    if hasattr(signal, "SIGPIPE"):
        signal.signal(signal.SIGPIPE, signal.SIG_IGN)

    # Sentence boundaries for streaming TTS splitting
    _SENTENCE_ENDS = frozenset("。！？!?\n")

    def _split_sentences(pending: str, new_text: str) -> tuple[list[str], str]:
        """Append new_text to pending, return (complete sentences, remainder)."""
        buf = pending + new_text
        sentences: list[str] = []
        start = 0
        for i, ch in enumerate(buf):
            if ch in _SENTENCE_ENDS:
                s = buf[start:i + 1].strip()
                if s:
                    sentences.append(s)
                start = i + 1
        return sentences, buf[start:]

    bus_task = asyncio.create_task(agent_loop.run())
    turn_done = asyncio.Event()
    turn_done.set()
    turn_response: list[tuple[str, dict]] = []
    renderer: StreamRenderer | None = None

    # TTS sentence queue — None is the stop sentinel
    tts_queue: asyncio.Queue[str | None] = asyncio.Queue()
    sentence_pending = ""   # text not yet dispatched to TTS

    async def _tts_worker():
        while True:
            sentence = await tts_queue.get()
            try:
                if sentence is None:
                    return
                if voice_controller:
                    await voice_controller.speak(sentence)
            finally:
                tts_queue.task_done()

    tts_worker_task = asyncio.create_task(_tts_worker())

    async def _consume_outbound():
        nonlocal renderer, sentence_pending
        while True:
            try:
                msg = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)

                if msg.metadata.get("_stream_delta"):
                    if renderer:
                        await renderer.on_delta(msg.content)
                    if voice_controller:
                        sentences, sentence_pending = _split_sentences(
                            sentence_pending, msg.content
                        )
                        for s in sentences:
                            await tts_queue.put(s)
                    continue
                if msg.metadata.get("_stream_end"):
                    if renderer:
                        await renderer.on_end(
                            resuming=msg.metadata.get("_resuming", False),
                        )
                    # Flush any remaining partial sentence
                    if voice_controller and sentence_pending.strip():
                        await tts_queue.put(sentence_pending.strip())
                        sentence_pending = ""
                    continue
                if msg.metadata.get("_streamed"):
                    turn_done.set()
                    continue
                if msg.metadata.get("_progress"):
                    # Skip if already shown via streaming
                    if not (renderer and renderer.streamed):
                        console.print(f"[dim]{msg.content}[/dim]")
                    continue

                if not turn_done.is_set():
                    if msg.content:
                        turn_response.append((msg.content, dict(msg.metadata or {})))
                        # Non-streaming response: enqueue full text for TTS
                        if voice_controller:
                            await tts_queue.put(msg.content)
                    turn_done.set()
                elif msg.content:
                    if not (renderer and renderer.streamed):
                        console.print(msg.content)

            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    outbound_task = asyncio.create_task(_consume_outbound())

    try:
        while True:
            if voice_controller:
                user_input = await voice_controller.listen()
                if not user_input:
                    continue
            else:
                try:
                    user_input = await asyncio.get_event_loop().run_in_executor(
                        None, lambda: input("[bold cyan]You[/bold cyan] › ")
                    )
                except EOFError:
                    break

            command = user_input.strip()
            if not command:
                continue
            if command.lower() in {"exit", "quit", "bye", "q"}:
                console.print("[cyan]JARVIS signing off. Goodbye, sir.[/cyan]")
                break

            turn_done.clear()
            turn_response.clear()
            sentence_pending = ""
            renderer = StreamRenderer(render_markdown=markdown)

            await bus.publish_inbound(InboundMessage(
                channel=cli_channel,
                sender_id="user",
                chat_id=cli_chat_id,
                content=user_input,
                metadata={"_wants_stream": True},
            ))

            # Wait for LLM to finish generating
            await turn_done.wait()
            # Wait for all TTS sentences to be spoken
            if voice_controller:
                await tts_queue.join()

            if turn_response:
                content, _ = turn_response[0]
                if not (renderer and renderer.streamed):
                    console.print(content)

    finally:
        tts_worker_task.cancel()
        bus_task.cancel()
        outbound_task.cancel()
        await agent_loop.close_mcp()
        with suppress_exceptions():
            await tts_worker_task
        with suppress_exceptions():
            await bus_task
        with suppress_exceptions():
            await outbound_task


class suppress_exceptions:
    """Suppress all exceptions (used in cleanup)."""
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return True
    def __enter__(self): return self
    def __exit__(self, *_): return True


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="jarvis",
        description="JARVIS — Just A Rather Very Intelligent System",
    )
    parser.add_argument("-m", "--message", help="Single message to send (non-interactive)")
    parser.add_argument(
        "-s", "--session",
        default="cli:direct",
        help="Session ID (default: cli:direct)",
    )
    parser.add_argument(
        "--no-markdown",
        action="store_true",
        help="Disable Markdown rendering",
    )
    parser.add_argument(
        "--logs",
        action="store_true",
        help="Show nanobot runtime logs",
    )
    parser.add_argument(
        "-v", "--voice",
        action="store_true",
        help="Enable voice mode: mic input (Groq Whisper STT) + TTS output",
    )
    parser.add_argument(
        "--openai-tts",
        action="store_true",
        help="Use OpenAI TTS instead of macOS say (requires OPENAI_API_KEY)",
    )
    args = parser.parse_args()

    asyncio.run(_run(
        message=args.message,
        session_id=args.session,
        markdown=not args.no_markdown,
        logs=args.logs,
        voice=args.voice,
        openai_tts=args.openai_tts,
    ))


if __name__ == "__main__":
    main()
