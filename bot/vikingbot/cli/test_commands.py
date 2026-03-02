"""
Test commands for vikingbot CLI.

This module provides integration with the tester framework.
If the tester framework is not available, it shows a friendly message.
"""

import sys
from pathlib import Path

import typer
from rich.console import Console

console = Console()

# Try to find the tester directory - search in common locations
def _find_tester_dir():
    """Find the tester directory by searching common locations."""
    # Common locations to search
    candidates = [
        Path(__file__).parent / "../../tests/tester",  # inside bot/tests/tester
        Path(__file__).parent / "../../../tester",      # repo structure: openviking/bot/vikingbot/cli
        Path(__file__).parent / "../../tester",         # repo structure: bot/vikingbot/cli
        Path.cwd() / "tests/tester",                    # current dir is bot
        Path.cwd() / "../tester",                       # current dir is bot
        Path.cwd() / "tester",                          # current dir is repo root
    ]

    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate.exists() and (candidate / "test_vikingbot.py").exists():
            return candidate

    return None


TESTER_DIR = _find_tester_dir()
_TESTER_IMPORTED = False
_TESTER_ERROR = None


def _import_tester():
    """Import the tester module if available. Returns (success, error_message)."""
    global _TESTER_IMPORTED, _TESTER_ERROR

    if _TESTER_IMPORTED:
        return True, None

    if _TESTER_ERROR:
        return False, _TESTER_ERROR

    if TESTER_DIR is None:
        _TESTER_ERROR = "Tester directory not found"
        return False, _TESTER_ERROR

    try:
        if str(TESTER_DIR) not in sys.path:
            sys.path.insert(0, str(TESTER_DIR))

        # Also ensure bot directory is in path
        bot_dir = TESTER_DIR / "../openviking/bot"
        if not bot_dir.exists():
            bot_dir = TESTER_DIR / "../bot"
        if bot_dir.exists() and str(bot_dir) not in sys.path:
            sys.path.insert(0, str(bot_dir))

        # Try importing
        import test_vikingbot

        _TESTER_IMPORTED = True
        return True, None
    except Exception as e:
        _TESTER_ERROR = str(e)
        return False, _TESTER_ERROR


def _show_tester_unavailable(error: str | None = None):
    """Show a friendly message when tester is not available."""
    console.print("\n[yellow]Tester framework not available[/yellow]\n")
    console.print("The tester framework is only available in the development environment.")
    console.print("\n[dim]Expected locations:[/dim]")
    console.print("  - tests/tester/ (from bot directory)")
    console.print("  - ../tester/ (from bot directory)")
    console.print("  - tester/ (from repo root)")
    if error:
        console.print(f"\n[dim]Error: {error}[/dim]")
    console.print()


test_app = typer.Typer(
    name="test",
    help="Run vikingbot tests (development only)",
    no_args_is_help=True,
)


@test_app.command("list")
def list_tests():
    """List all available tests."""
    success, error = _import_tester()
    if not success:
        _show_tester_unavailable(error)
        raise typer.Exit(1)

    try:
        from test_vikingbot import list_tests as _list_tests

        _list_tests()
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@test_app.command("spec")
def show_spec(test: str):
    """Show detailed spec for a test."""
    success, error = _import_tester()
    if not success:
        _show_tester_unavailable(error)
        raise typer.Exit(1)

    try:
        from test_vikingbot import show_spec as _show_spec

        _show_spec(test)
    except SystemExit:
        pass
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)


@test_app.command("run")
def run_tests(
    tests: list[str] = typer.Argument(None, help="Tests to run (leave empty for all)"),
):
    """Run tests."""
    success, error = _import_tester()
    if not success:
        _show_tester_unavailable(error)
        raise typer.Exit(1)

    try:
        from test_vikingbot import run_tests as _run_tests

        exit_code = _run_tests(tests)
        raise typer.Exit(exit_code)
    except SystemExit:
        raise
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        raise typer.Exit(1)
