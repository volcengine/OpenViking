#!/usr/bin/env python3
"""
Query - CLI tool to search and generate answers using OpenViking + LLM
"""

import argparse
import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.recipe import Recipe
from rich import box
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

console = Console()

# Optimal line width for readability: 50-75 characters (66 is sweet spot)
# Using 72 for balance between readability and information density
OPTIMAL_LINE_WIDTH = 72

# Panel width: 72 chars + 4 for borders + 4 for padding = 80
# But we want it more constrained for better visual appearance
PANEL_WIDTH = 78


def show_loading_with_spinner(message: str, target_func, *args, **kwargs):
    """
    Show a loading spinner while a function executes.

    Args:
        message: Message to display
        target_func: Function to execute
        *args, **kwargs: Arguments to pass to target_func

    Returns:
        Result from target_func
    """
    spinner = Spinner("dots", text=message)
    result = None
    exception = None

    def run_target():
        nonlocal result, exception
        try:
            result = target_func(*args, **kwargs)
        except Exception as e:
            exception = e

    # Start of target function in a thread
    thread = threading.Thread(target=run_target)
    thread.start()

    # Show spinner while thread is running
    # Use transient=True to auto-clear when done
    with Live(spinner, console=console, refresh_per_second=10, transient=True):
        thread.join()

    # Add newline for space before answer panel
    console.print()

    # Raise exception if one occurred
    if exception:
        raise exception

    return result


def query(
    question: str,
    config_path: str = "./ov.conf",
    data_path: str = "./data",
    top_k: int = 5,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    score_threshold: float = 0.2,
    verbose: bool = False,
):
    """
    Query the database and generate an answer using LLM

    Args:
        question: The question to ask
        config_path: Path to config file
        data_path: Path to data directory
        top_k: Number of search results to use as context
        temperature: LLM temperature (0.0-1.0)
        max_tokens: Maximum tokens to generate
        score_threshold: Minimum relevance score for search results (default: 0.2)
        verbose: Show detailed information
    """
    if verbose:
        # Config info table
        info_table = Table(show_header=False, box=None, padding=(0, 2))
        info_table.add_column("Key", style="bold green")
        info_table.add_column("Value")
        info_table.add_row("Config", config_path)
        info_table.add_row("Data", data_path)
        info_table.add_row("Top-K", str(top_k))
        info_table.add_row("Temperature", str(temperature))
        info_table.add_row("Max tokens", str(max_tokens))
        console.print(Panel(info_table, style="bold blue", padding=(0, 1), width=PANEL_WIDTH))
        console.print()

    # Initialize pipeline
    try:
        pipeline = Recipe(config_path=config_path, data_path=data_path)
    except Exception as e:
        console.print(
            Panel(f"❌ Error initializing pipeline: {e}", style="bold red", padding=(0, 1))
        )
        return False

    try:
        # Display question with constrained width
        question_text = Text(question, style="bold yellow")
        console.print(
            Panel(
                question_text,
                title="✅ Roger That",
                style="bold",
                padding=(0, 1),
                width=PANEL_WIDTH,
            )
        )
        console.print()

        # Query with loading spinner
        result = show_loading_with_spinner(
            "Wait a sec...",
            pipeline.query,
            user_query=question,
            search_top_k=top_k,
            temperature=temperature,
            max_tokens=max_tokens,
            score_threshold=score_threshold,
        )
        answer_text = Text(result["answer"], style="white")
        console.print(
            Panel(
                answer_text,
                title="🍔 Check This Out",
                style="bold bright_cyan",
                padding=(1, 1),
                width=PANEL_WIDTH,
            )
        )
        console.print()

        # Show sources
        if result["context"]:
            sources_table = Table(
                title=f"📚 Sources ({len(result['context'])} documents)",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold magenta",
                title_style="bold magenta",
            )
            sources_table.add_column("#", style="cyan", width=4)
            sources_table.add_column("File", style="bold white")
            sources_table.add_column("Relevance", style="green", justify="right")

            if verbose:
                sources_table.add_column("URI", style="dim")
                sources_table.add_column("Preview", style="dim")

            for i, ctx in enumerate(result["context"], 1):
                uri_parts = ctx["uri"].split("/")
                filename = uri_parts[-1] if uri_parts else ctx["uri"]
                score_text = Text(f"{ctx['score']:.4f}", style="bold green")

                if verbose:
                    preview = (
                        ctx["content"][:100] + "..."
                        if len(ctx["content"]) > 100
                        else ctx["content"]
                    )
                    sources_table.add_row(str(i), filename, score_text, ctx["uri"], preview)
                else:
                    sources_table.add_row(str(i), filename, score_text)

            console.print(sources_table)
        else:
            console.print(
                Panel(
                    "⚠️  No relevant sources found",
                    style="yellow",
                    padding=(0, 1),
                    width=PANEL_WIDTH,
                )
            )

        return True

    except Exception as e:
        console.print(Panel(f"❌ Error during query: {e}", style="bold red", padding=(0, 1)))
        import traceback

        traceback.print_exc()
        return False

    finally:
        pipeline.close()


def main():
    parser = argparse.ArgumentParser(
        description="Search database and generate answers using RAG (Retrieval-Augmented Generation)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic query
  uv run query.py "What is prompt engineering?"

  # Query with more context
  uv run query.py "Explain chain of thought prompting" --top-k 10

  # Adjust creativity (temperature)
  uv run query.py "Give me creative prompt ideas" --temperature 0.9

  # Get detailed output
  uv run query.py "What are the best practices?" --verbose

  # Use custom config and data
  uv run query.py "Question?" --config ./my.conf --data ./mydata

  # Enable debug logging
  OV_DEBUG=1 uv run query.py "Question?"

Temperature Guide:
  0.0-0.3  → Deterministic, focused, consistent (good for facts)
  0.4-0.7  → Balanced creativity and accuracy (default: 0.7)
  0.8-1.0  → Creative, varied, exploratory (good for brainstorming)

Top-K Guide:
  3-5   → Quick, focused answers (default: 5)
  5-10  → More comprehensive context
  10+   → Maximum context (may include less relevant info)

Score Threshold Guide:
  0.0-0.1 → Very permissive, includes low relevance results
  0.2      → Balanced (default)
  0.3-0.5  → Strict, only highly relevant results
        """,
    )

    parser.add_argument("question", type=str, help="Your question to ask the LLM")

    parser.add_argument(
        "--config", type=str, default="./ov.conf", help="Path to config file (default: ./ov.conf)"
    )

    parser.add_argument(
        "--data", type=str, default="./data", help="Path to data directory (default: ./data)"
    )

    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of search results to use as context (default: 5)",
    )

    parser.add_argument(
        "--temperature", type=float, default=0.7, help="LLM temperature 0.0-1.0 (default: 0.7)"
    )

    parser.add_argument(
        "--max-tokens", type=int, default=2048, help="Maximum tokens to generate (default: 2048)"
    )

    parser.add_argument(
        "--score-threshold",
        type=float,
        default=0.2,
        help="Minimum relevance score for search results (default: 0.2)",
    )

    parser.add_argument("--verbose", "-v", action="store_true", help="Show detailed information")

    args = parser.parse_args()

    # Validate temperature
    if not 0.0 <= args.temperature <= 1.0:
        console.print(
            Panel(
                "❌ Error: Temperature must be between 0.0 and 1.0",
                style="bold red",
                padding=(0, 1),
            )
        )
        sys.exit(1)

    # Validate top-k
    if args.top_k < 1:
        console.print(Panel("❌ Error: top-k must be at least 1", style="bold red", padding=(0, 1)))
        sys.exit(1)

    if not 0.0 <= args.score_threshold <= 1.0:
        console.print(
            Panel(
                "❌ Error: score-threshold must be between 0.0 and 1.0",
                style="bold red",
                padding=(0, 1),
            )
        )
        sys.exit(1)

    # Run query
    success = query(
        question=args.question,
        config_path=args.config,
        data_path=args.data,
        top_k=args.top_k,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        score_threshold=args.score_threshold,
        verbose=args.verbose,
    )

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
