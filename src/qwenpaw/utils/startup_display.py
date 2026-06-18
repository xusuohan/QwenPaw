# -*- coding: utf-8 -*-
"""Fancy startup display utilities using rich."""
import sys
from typing import Optional, Tuple

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.tree import Tree


def print_ready_banner(
    api_info: Optional[Tuple[str, int]] = None,
    elapsed_seconds: Optional[float] = None,
) -> None:
    """Print a fancy QwenPaw ready banner with rich formatting.

    Args:
        api_info: Optional tuple of (host, port) for the server URL.
                 If None, displays a generic ready message.
        elapsed_seconds: Optional startup time in seconds to display.

    Example:
        >>> print_ready_banner(("127.0.0.1", 8088), 2.345)
        # Displays a fancy panel with the server URL and startup time
        >>> print_ready_banner()
        # Displays a generic ready message
    """
    # When stdout is not a TTY (subprocess.PIPE in desktop_cmd, file
    # redirect, etc.), rich's legacy Windows renderer calls WriteConsoleW
    # which only works on real console handles and raises
    # "OSError: [Errno 22] Invalid argument" on flush.  Fall back to plain
    # ASCII in that case — the banner is cosmetic and pipes don't render
    # ANSI colors usefully anyway.
    if not sys.stdout.isatty():
        url = (
            f"http://{api_info[0]}:{api_info[1]}" if api_info else None
        )
        print()
        print("  QwenPaw ready")
        if url:
            print(f"  Address: {url}")
        if elapsed_seconds is not None:
            print(f"  Startup: {elapsed_seconds:.3f}s")
        print()
        return

    console = Console()

    # Extra spacing before banner
    console.print()

    if api_info:
        host, port = api_info
        url = f"http://{host}:{port}"

        # Create tree structure (Docker/K8s style)
        tree = Tree(
            "[bold green]✓[/bold green] [bold]QwenPaw[/bold]",
            guide_style="bright_black",
        )
        tree.add("[dim]Status:[/dim]  [bold green]Ready[/bold green]")
        tree.add(
            f"[dim]Address:[/dim] [blue underline]{url}[/blue underline]",
        )
        if elapsed_seconds is not None:
            tree.add(
                f"[dim]Startup:[/dim] [yellow]{elapsed_seconds:.3f}s[/yellow]",
            )

        # Wrap in clean panel (Apple style)
        panel = Panel(
            tree,
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )
    else:
        # Simple ready message without URL
        tree = Tree(
            "[bold green]✓[/bold green] [bold]QwenPaw[/bold]",
            guide_style="bright_black",
        )
        tree.add("[dim]Status:[/dim]  [bold green]Ready[/bold green]")
        if elapsed_seconds is not None:
            tree.add(
                f"[dim]Startup:[/dim] [yellow]{elapsed_seconds:.3f}s[/yellow]",
            )

        panel = Panel(
            tree,
            border_style="green",
            box=box.ROUNDED,
            padding=(1, 2),
            expand=False,
        )

    console.print(panel)
    console.print()
