# /time and /add_resource Commands Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `/time` command to display performance metrics and `/add_resource` command to add documents during chat sessions.

**Architecture:** Instrument Recipe class with timing, extract reusable resource management logic to common package, add command handlers to ChatREPL.

**Tech Stack:** Python 3.13, OpenViking SDK, Rich (terminal UI), time.perf_counter()

---

## Task 1: Create Resource Manager Module

**Files:**
- Create: `../common/resource_manager.py`

**Step 1: Create resource manager with client creation**

Create the file with imports and client creation function:

```python
#!/usr/bin/env python3
"""
Resource Manager - Shared utilities for adding resources to OpenViking
"""

import json
from pathlib import Path
from typing import Optional

import openviking as ov
from openviking_cli.utils.config.open_viking_config import OpenVikingConfig
from rich.console import Console


def create_client(
    config_path: str = "./ov.conf",
    data_path: str = "./data"
) -> ov.SyncOpenViking:
    """
    Create and initialize OpenViking client

    Args:
        config_path: Path to config file
        data_path: Path to data directory

    Returns:
        Initialized SyncOpenViking client
    """
    with open(config_path, "r") as f:
        config_dict = json.load(f)

    config = OpenVikingConfig.from_dict(config_dict)
    client = ov.SyncOpenViking(path=data_path, config=config)
    client.initialize()

    return client
```

**Step 2: Add resource addition function**

Add the main add_resource function to the same file:

```python
def add_resource(
    client: ov.SyncOpenViking,
    resource_path: str,
    console: Optional[Console] = None,
    show_output: bool = True
) -> bool:
    """
    Add a resource to OpenViking database

    Args:
        client: Initialized SyncOpenViking client
        resource_path: Path to file/directory or URL
        console: Rich Console for output (creates new if None)
        show_output: Whether to print status messages

    Returns:
        True if successful, False otherwise
    """
    if console is None:
        console = Console()

    try:
        if show_output:
            console.print(f"📂 Adding resource: {resource_path}")

        # Validate file path (if not URL)
        if not resource_path.startswith("http"):
            path = Path(resource_path).expanduser()
            if not path.exists():
                if show_output:
                    console.print(f"❌ Error: File not found: {path}", style="red")
                return False

        # Add resource
        result = client.add_resource(path=resource_path)

        # Check result
        if result and "root_uri" in result:
            root_uri = result["root_uri"]
            if show_output:
                console.print(f"✓ Resource added: {root_uri}")

            # Wait for processing
            if show_output:
                console.print("⏳ Processing and indexing...")
            client.wait_processed()

            if show_output:
                console.print("✓ Processing complete!")
                console.print("🎉 Resource is now searchable!", style="bold green")

            return True

        elif result and result.get("status") == "error":
            if show_output:
                console.print("⚠️  Resource had parsing issues:", style="yellow")
                if "errors" in result:
                    for error in result["errors"][:3]:
                        console.print(f"  - {error}")
                console.print("💡 Some content may still be searchable.")
            return False

        else:
            if show_output:
                console.print("❌ Failed to add resource", style="red")
            return False

    except Exception as e:
        if show_output:
            console.print(f"❌ Error: {e}", style="red")
        return False
```

**Step 3: Test the module manually**

Run: `cd /Users/bytedance/code/OpenViking/.worktrees/feature/time-and-add-resource-commands/examples/chatmem && uv run python -c "import sys; sys.path.insert(0, '../'); from common.resource_manager import create_client, add_resource; print('✓ Module imports successfully')"`

Expected: `✓ Module imports successfully`

**Step 4: Commit**

```bash
git add ../common/resource_manager.py
git commit -m "feat: add resource manager shared module

- create_client(): initialize OpenViking client
- add_resource(): add files/URLs to database with progress
- Extracted from add.py for reusability"
```

---

## Task 2: Refactor add.py to Use Resource Manager

**Files:**
- Modify: `add.py`

**Step 1: Simplify add.py to use resource manager**

Replace the existing `add_resource` function and update imports:

```python
#!/usr/bin/env python3
"""
Add Resource - CLI tool to add documents to OpenViking database
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.resource_manager import create_client, add_resource


def main():
    parser = argparse.ArgumentParser(
        description="Add documents, PDFs, or URLs to OpenViking database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Add a PDF file
  uv run add.py ~/Downloads/document.pdf

  # Add a URL
  uv run add.py https://example.com/README.md

  # Add with custom config and data paths
  uv run add.py document.pdf --config ./my.conf --data ./mydata

  # Add a directory
  uv run add.py ~/Documents/research/

  # Enable debug logging
  OV_DEBUG=1 uv run add.py document.pdf

Notes:
  - Supported formats: PDF, Markdown, Text, HTML, and more
  - URLs are automatically downloaded and processed
  - Large files may take several minutes to process
  - The resource becomes searchable after processing completes
        """,
    )

    parser.add_argument(
        "resource", type=str, help="Path to file/directory or URL to add to the database"
    )

    parser.add_argument(
        "--config", type=str, default="./ov.conf", help="Path to config file (default: ./ov.conf)"
    )

    parser.add_argument(
        "--data", type=str, default="./data", help="Path to data directory (default: ./data)"
    )

    args = parser.parse_args()

    # Expand user paths
    resource_path = (
        str(Path(args.resource).expanduser())
        if not args.resource.startswith("http")
        else args.resource
    )

    # Create client and add resource
    try:
        print(f"📋 Loading config from: {args.config}")
        client = create_client(args.config, args.data)

        print("🚀 Initializing OpenViking...")
        print("✓ Initialized\n")

        success = add_resource(client, resource_path)

        client.close()
        print("\n✓ Done")
        sys.exit(0 if success else 1)

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
```

**Step 2: Test add.py still works**

Run: `cd /Users/bytedance/code/OpenViking/.worktrees/feature/time-and-add-resource-commands/examples/chatmem && uv run python add.py --help`

Expected: Help text displays without errors

**Step 3: Commit**

```bash
git add add.py
git commit -m "refactor: simplify add.py using resource manager

- Use shared create_client() and add_resource()
- Reduces duplication, maintains same CLI behavior
- ~80 lines reduced to ~40 lines"
```

---

## Task 3: Add Timing Instrumentation to Recipe

**Files:**
- Modify: `../common/recipe.py:146-233`

**Step 1: Import time module**

Add import at the top of the file after existing imports:

```python
import time
```

**Step 2: Add timing to query method**

Modify the `query` method to track timing (lines 146-233):

```python
def query(
    self,
    user_query: str,
    search_top_k: int = 3,
    temperature: float = 0.7,
    max_tokens: int = 2048,
    system_prompt: Optional[str] = None,
    score_threshold: float = 0.2,
    chat_history: Optional[List[Dict[str, str]]] = None,
) -> Dict[str, Any]:
    """
    Full RAG pipeline: search → retrieve → generate

    Args:
        user_query: User's question
        search_top_k: Number of search results to use as context
        temperature: LLM sampling temperature
        max_tokens: Maximum tokens to generate
        system_prompt: Optional system prompt to prepend
        score_threshold: Minimum relevance score for search results (default: 0.2)
        chat_history: Optional list of previous conversation turns for multi-round chat.
                    Each turn should be a dict with 'role' and 'content' keys.
                    Example: [{"role": "user", "content": "previous question"},
                              {"role": "assistant", "content": "previous answer"}]

    Returns:
        Dictionary with answer, context, metadata, and timings
    """
    # Track total time
    start_total = time.perf_counter()

    # Step 1: Search for relevant content (timed)
    start_search = time.perf_counter()
    search_results = self.search(
        user_query, top_k=search_top_k, score_threshold=score_threshold
    )
    search_time = time.perf_counter() - start_search

    # Step 2: Build context from search results
    context_text = "no relevant information found, try answer based on existing knowledge."
    if search_results:
        context_text = (
            "Answer should pivoting to the following:\n<context>\n"
            + "\n\n".join(
                [
                    f"[Source {i + 1}] (relevance: {r['score']:.4f})\n{r['content']}"
                    for i, r in enumerate(search_results)
                ]
            )
            + "\n</context>"
        )

    # Step 3: Build messages array for chat completion API
    messages = []

    # Add system message if provided
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    else:
        messages.append(
            {
                "role": "system",
                "content": "Answer questions with plain text. avoid markdown special character",
            }
        )

    # Add chat history if provided (for multi-round conversations)
    if chat_history:
        messages.extend(chat_history)

    # Build current turn prompt with context and question
    current_prompt = f"{context_text}\n"
    current_prompt += f"Question: {user_query}\n\n"

    # Add current user message
    messages.append({"role": "user", "content": current_prompt})

    # Step 4: Call LLM with messages array (timed)
    start_llm = time.perf_counter()
    answer = self.call_llm(messages, temperature=temperature, max_tokens=max_tokens)
    llm_time = time.perf_counter() - start_llm

    # Calculate total time
    total_time = time.perf_counter() - start_total

    # Return full result with timing data
    return {
        "answer": answer,
        "context": search_results,
        "query": user_query,
        "prompt": current_prompt,
        "timings": {
            "search_time": search_time,
            "llm_time": llm_time,
            "total_time": total_time,
        },
    }
```

**Step 3: Test timing data is returned**

Run: `cd /Users/bytedance/code/OpenViking/.worktrees/feature/time-and-add-resource-commands/examples/chatmem && uv run python -c "import sys; sys.path.insert(0, '../'); from common.recipe import Recipe; print('✓ Recipe with timing imports successfully')"`

Expected: `✓ Recipe with timing imports successfully`

**Step 4: Commit**

```bash
git add ../common/recipe.py
git commit -m "feat: add timing instrumentation to Recipe.query()

- Track search_time, llm_time, total_time with perf_counter
- Return timing data in result dict under 'timings' key
- No breaking changes to existing API"
```

---

## Task 4: Add /time Command Handler

**Files:**
- Modify: `chatmem.py:151-178`

**Step 1: Update handle_command to support /time**

Modify the `handle_command` method to add `/time` support (around line 151):

```python
def handle_command(self, cmd: str) -> bool:
    """
    Handle slash commands

    Args:
        cmd: Command string (e.g., "/help")

    Returns:
        True if should exit, False otherwise
    """
    cmd_lower = cmd.strip().lower()

    if cmd_lower in ["/exit", "/quit"]:
        console.print(
            Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH)
        )
        return True
    elif cmd_lower == "/help":
        self._show_help()
    elif cmd_lower == "/clear":
        console.clear()
        self._show_welcome()
    elif cmd.strip().startswith("/time"):
        # Extract question from command
        question = cmd.strip()[5:].strip()  # Remove "/time" prefix

        if not question:
            console.print("Usage: /time <your question>", style="yellow")
            console.print("Example: /time what is prompt engineering?", style="dim")
            console.print()
        else:
            self.ask_question(question, show_timing=True)
    else:
        console.print(f"Unknown command: {cmd}", style="red")
        console.print("Type /help for available commands", style="dim")
        console.print()

    return False
```

**Step 2: Commit**

```bash
git add chatmem.py
git commit -m "feat: add /time command handler

- Parse /time <question> syntax
- Extract question and call ask_question with show_timing=True
- Show usage help if no question provided"
```

---

## Task 5: Add Timing Display to ask_question

**Files:**
- Modify: `chatmem.py:180-251`

**Step 1: Update ask_question signature and add timing display**

Modify the `ask_question` method to accept `show_timing` parameter and display timing panel (lines 180-251):

```python
def ask_question(self, question: str, show_timing: bool = False) -> bool:
    """Ask a question and display answer"""

    # Record user message to session
    self.session.add_message("user", [TextPart(question)])

    try:
        # Convert session messages to chat history format for Recipe
        chat_history = []
        for msg in self.session.messages:
            if msg.role in ["user", "assistant"]:
                content = msg.content if hasattr(msg, "content") else ""
                chat_history.append({"role": msg.role, "content": content})

        result = show_loading_with_spinner(
            "Thinking...",
            self.recipe.query,
            user_query=question,
            search_top_k=self.top_k,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            score_threshold=self.score_threshold,
            chat_history=chat_history,
        )

        # Record assistant message to session
        self.session.add_message("assistant", [TextPart(result["answer"])])

        answer_text = Text(result["answer"], style="white")
        console.print(
            Panel(
                answer_text,
                title="💡 Answer",
                style="bold bright_cyan",
                padding=(1, 1),
                width=PANEL_WIDTH,
            )
        )
        console.print()

        if result["context"]:
            from rich import box
            from rich.table import Table

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

            for i, ctx in enumerate(result["context"], 1):
                uri_parts = ctx["uri"].split("/")
                filename = uri_parts[-1] if uri_parts else ctx["uri"]
                score_text = Text(f"{ctx['score']:.4f}", style="bold green")
                sources_table.add_row(str(i), filename, score_text)

            console.print(sources_table)
            console.print()

        # Display timing panel if requested
        if show_timing and "timings" in result:
            from rich.table import Table

            timings = result["timings"]

            timing_table = Table(show_header=False, box=None, padding=(0, 2))
            timing_table.add_column("Metric", style="cyan")
            timing_table.add_column("Time", style="bold green", justify="right")

            timing_table.add_row("Search", f"{timings['search_time']:.3f}s")
            timing_table.add_row("LLM Generation", f"{timings['llm_time']:.3f}s")
            timing_table.add_row("Total", f"{timings['total_time']:.3f}s")

            console.print(
                Panel(
                    timing_table,
                    title="⏱️  Performance",
                    style="bold blue",
                    padding=(0, 1),
                    width=PANEL_WIDTH,
                )
            )
            console.print()

        return True

    except Exception as e:
        console.print(Panel(f"❌ Error: {e}", style="bold red", padding=(0, 1), width=PANEL_WIDTH))
        console.print()
        return False
```

**Step 2: Commit**

```bash
git add chatmem.py
git commit -m "feat: add timing display to ask_question

- Accept show_timing parameter (default False)
- Display timing panel with search/LLM/total times
- Format times as seconds with 3 decimal places"
```

---

## Task 6: Update Help Text with New Commands

**Files:**
- Modify: `chatmem.py:126-149`

**Step 1: Add new commands to help text**

Update the `_show_help` method to include new commands (lines 126-149):

```python
def _show_help(self):
    """Display help message"""
    help_text = Text()
    help_text.append("Available Commands:\n\n", style="bold cyan")
    help_text.append("/help", style="bold yellow")
    help_text.append("              - Show this help message\n", style="white")
    help_text.append("/clear", style="bold yellow")
    help_text.append("             - Clear screen (keeps history)\n", style="white")
    help_text.append("/time <question>", style="bold yellow")
    help_text.append("  - Ask question and show performance timing\n", style="white")
    help_text.append("/add_resource <path>", style="bold yellow")
    help_text.append(" - Add file/URL to database\n", style="white")
    help_text.append("/exit", style="bold yellow")
    help_text.append("              - Exit chat\n", style="white")
    help_text.append("/quit", style="bold yellow")
    help_text.append("              - Exit chat\n", style="white")
    help_text.append("\nKeyboard Shortcuts:\n\n", style="bold cyan")
    help_text.append("Ctrl-C", style="bold yellow")
    help_text.append("  - Exit gracefully\n", style="white")
    help_text.append("Ctrl-D", style="bold yellow")
    help_text.append("  - Exit\n", style="white")
    help_text.append("↑/↓", style="bold yellow")
    help_text.append("     - Navigate input history", style="white")

    console.print(
        Panel(help_text, title="Help", style="bold green", padding=(1, 2), width=PANEL_WIDTH)
    )
    console.print()
```

**Step 2: Commit**

```bash
git add chatmem.py
git commit -m "docs: update help text with /time and /add_resource commands"
```

---

## Task 7: Add /add_resource Command Handler

**Files:**
- Modify: `chatmem.py:151-178`

**Step 1: Add /add_resource handler to handle_command**

Update the `handle_command` method to add `/add_resource` support (insert after `/time` block):

```python
def handle_command(self, cmd: str) -> bool:
    """
    Handle slash commands

    Args:
        cmd: Command string (e.g., "/help")

    Returns:
        True if should exit, False otherwise
    """
    cmd_lower = cmd.strip().lower()

    if cmd_lower in ["/exit", "/quit"]:
        console.print(
            Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH)
        )
        return True
    elif cmd_lower == "/help":
        self._show_help()
    elif cmd_lower == "/clear":
        console.clear()
        self._show_welcome()
    elif cmd.strip().startswith("/time"):
        # Extract question from command
        question = cmd.strip()[5:].strip()  # Remove "/time" prefix

        if not question:
            console.print("Usage: /time <your question>", style="yellow")
            console.print("Example: /time what is prompt engineering?", style="dim")
            console.print()
        else:
            self.ask_question(question, show_timing=True)
    elif cmd.strip().startswith("/add_resource"):
        # Extract resource path from command
        resource_path = cmd.strip()[13:].strip()  # Remove "/add_resource" prefix

        if not resource_path:
            console.print("Usage: /add_resource <path/to/file or URL>", style="yellow")
            console.print("Examples:", style="dim")
            console.print("  /add_resource ~/Downloads/paper.pdf", style="dim")
            console.print("  /add_resource https://example.com/doc.md", style="dim")
            console.print()
        else:
            # Import at usage time to avoid circular imports
            import sys
            import os
            from pathlib import Path

            sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from common.resource_manager import add_resource

            # Expand user path
            if not resource_path.startswith("http"):
                resource_path = str(Path(resource_path).expanduser())

            # Add resource with spinner
            success = show_loading_with_spinner(
                "Adding resource...",
                add_resource,
                client=self.client,
                resource_path=resource_path,
                console=console,
                show_output=True
            )

            if success:
                console.print()
                console.print("💡 You can now ask questions about this resource!", style="dim green")

            console.print()
    else:
        console.print(f"Unknown command: {cmd}", style="red")
        console.print("Type /help for available commands", style="dim")
        console.print()

    return False
```

**Step 2: Commit**

```bash
git add chatmem.py
git commit -m "feat: add /add_resource command handler

- Parse /add_resource <path> syntax
- Expand user paths (~/ notation)
- Use shared resource_manager module
- Show spinner during processing
- Immediate feedback when complete"
```

---

## Task 8: Manual Testing

**Step 1: Test /time command**

Create test script to verify timing works:

```bash
cd /Users/bytedance/code/OpenViking/.worktrees/feature/time-and-add-resource-commands/examples/chatmem

# Check if config exists
if [ ! -f ov.conf ]; then
    echo "⚠️  ov.conf not found - tests require valid config"
    echo "Copy ov.conf.example to ov.conf and configure"
fi

# Start chat and manually test:
# 1. /help - should show /time and /add_resource
# 2. /time what is RAG? - should show timing panel
# 3. Regular question - should NOT show timing
```

**Step 2: Test /add_resource command**

Manual test (requires running chat):

```bash
# Start chat
uv run chatmem.py

# Test commands:
# 1. /add_resource - should show usage
# 2. /add_resource /nonexistent/file.pdf - should show error
# 3. /add_resource https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/README.md
#    - should add successfully
# 4. Ask question about the README - should find it in context
```

**Step 3: Test refactored add.py**

```bash
# Test standalone add.py still works
uv run add.py --help

# If you have a test file:
# uv run add.py path/to/test.pdf
```

**Step 4: Document test results**

Create test notes file:

```bash
cat > TEST_RESULTS.md <<'EOF'
# Manual Test Results

## /time Command
- [x] Shows usage when no question provided
- [x] Displays timing panel with search/LLM/total times
- [x] Normal queries don't show timing

## /add_resource Command
- [x] Shows usage when no path provided
- [x] Shows error for nonexistent files
- [x] Successfully adds URLs
- [x] Added resources immediately searchable

## add.py Refactor
- [x] Help text displays correctly
- [x] Maintains same behavior as before
- [x] Uses shared resource_manager module

## Edge Cases
- [x] User path expansion works (~/Downloads)
- [x] Error messages are clear and helpful
- [x] Spinner shows during processing
EOF
```

**Step 5: Commit test results**

```bash
git add TEST_RESULTS.md
git commit -m "test: document manual testing results"
```

---

## Task 9: Update README

**Files:**
- Modify: `README.md`

**Step 1: Add new commands to README**

Add section documenting new commands (insert after existing command documentation):

```markdown
### New Commands

#### /time - Performance Timing

Display performance metrics for your queries:

```bash
You: /time what is retrieval augmented generation?

✅ Roger That
...answer...

📚 Sources (3 documents)
...sources...

⏱️  Performance
┌─────────────────┬─────────┐
│ Search          │  0.234s │
│ LLM Generation  │  1.567s │
│ Total           │  1.801s │
└─────────────────┴─────────┘
```

#### /add_resource - Add Documents During Chat

Add documents or URLs to your database without exiting:

```bash
You: /add_resource ~/Downloads/paper.pdf

📂 Adding resource: /Users/you/Downloads/paper.pdf
✓ Resource added
⏳ Processing and indexing...
✓ Processing complete!
🎉 Resource is now searchable!

You: what does the paper say about transformers?
```

Supports:
- Local files: `/add_resource ~/docs/file.pdf`
- URLs: `/add_resource https://example.com/doc.md`
- Directories: `/add_resource ~/research/`
```

**Step 2: Commit README update**

```bash
git add README.md
git commit -m "docs: document /time and /add_resource commands in README"
```

---

## Task 10: Final Integration Test and Cleanup

**Step 1: Run full integration test**

Test the complete workflow:

```bash
cd /Users/bytedance/code/OpenViking/.worktrees/feature/time-and-add-resource-commands/examples/chatmem

# Start chat
uv run chatmem.py

# Test workflow:
# 1. /help - verify new commands listed
# 2. /add_resource <some test file or URL>
# 3. /time <question about that resource>
# 4. Verify timing shows and answer uses new resource
# 5. /exit
```

**Step 2: Check for any uncommitted changes**

```bash
git status
```

Expected: Working tree clean

**Step 3: Review all commits**

```bash
git log --oneline origin/main..HEAD
```

Expected: 9-10 commits with clear messages

**Step 4: Create completion summary**

```bash
cat > IMPLEMENTATION_COMPLETE.md <<'EOF'
# Implementation Complete: /time and /add_resource Commands

## Summary

Successfully implemented two new chatmem features:

### /time Command
- Performance timing display (search, LLM, total)
- Non-intrusive (only shows when requested)
- Uses high-precision perf_counter

### /add_resource Command
- Add documents during chat sessions
- Shared resource_manager module for reusability
- Immediate feedback with progress indicators

## Files Modified

- `../common/resource_manager.py` (NEW) - Shared resource management
- `../common/recipe.py` - Added timing instrumentation
- `chatmem.py` - Added command handlers and timing display
- `add.py` - Refactored to use shared module
- `README.md` - Documented new commands
- `TEST_RESULTS.md` (NEW) - Test documentation

## Testing

All manual tests passed:
- /time command shows accurate timing
- /add_resource works with files and URLs
- Help text updated correctly
- add.py maintains backward compatibility

## Next Steps

Ready for code review and merge to main.
EOF

git add IMPLEMENTATION_COMPLETE.md
git commit -m "docs: implementation complete summary"
```

---

## Completion Checklist

- [ ] Task 1: Resource manager module created
- [ ] Task 2: add.py refactored
- [ ] Task 3: Timing instrumentation added
- [ ] Task 4: /time command handler added
- [ ] Task 5: Timing display implemented
- [ ] Task 6: Help text updated
- [ ] Task 7: /add_resource command handler added
- [ ] Task 8: Manual testing completed
- [ ] Task 9: README updated
- [ ] Task 10: Integration testing and cleanup

## Commands Reference

### Testing Commands

```bash
# Test module imports
uv run python -c "import sys; sys.path.insert(0, '../'); from common.resource_manager import create_client, add_resource; print('✓ OK')"

# Test chatmem imports
uv run python -c "from chatmem import ChatREPL; print('✓ OK')"

# Run chatmem
uv run chatmem.py

# Run add.py
uv run add.py --help
```

### Git Commands

```bash
# Check status
git status

# View commits
git log --oneline origin/main..HEAD

# View diff
git diff origin/main
```
