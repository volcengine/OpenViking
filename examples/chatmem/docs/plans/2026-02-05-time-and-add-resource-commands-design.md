# Design: /time and /add_resource Commands

**Date:** 2026-02-05
**Status:** Approved
**Author:** AI Assistant with User Input

## Overview

This design document describes the implementation of two new features for the chatmem application:

1. **`/time` command** - Display performance timing breakdown (search time, LLM generation time)
2. **`/add_resource` command** - Add documents/URLs to the database during chat sessions

Both features integrate seamlessly into the existing chatmem REPL interface.

## Requirements

### Feature 1: /time Command

- **Usage:** `/time <question>` - Ask a question and show timing information
- **Display:** Show dedicated timing panel after answer with breakdown:
  - Search time (semantic search duration)
  - LLM generation time (API call duration)
  - Total time (end-to-end duration)
- **Behavior:** Only show timing when explicitly requested (keeps UI clean by default)

### Feature 2: /add_resource Command

- **Usage:** `/add_resource <path or URL>` - Add a resource to the database
- **Location:** Shared utility in `common/` package + command handler in `chatmem.py`
- **Behavior:** Block and wait with spinner until resource is fully processed and indexed
- **Benefits:**
  - In-chat resource management (no need to exit and run separate script)
  - Reusable across multiple scripts
  - Consistent with existing `add.py` behavior

## Architecture

### High-Level Design

```
┌─────────────────────────────────────────────────────────────┐
│                        chatmem.py                           │
│  ┌───────────────────────────────────────────────────────┐  │
│  │         handle_command(cmd: str)                      │  │
│  │  - /help, /clear, /exit (existing)                    │  │
│  │  - /time <question>  (NEW)                            │  │
│  │  - /add_resource <path>  (NEW)                        │  │
│  └───────────────────────────────────────────────────────┘  │
│                           │                                 │
│                           ▼                                 │
│  ┌───────────────────────────────────────────────────────┐  │
│  │    ask_question(question, show_timing=False)          │  │
│  │  - Calls Recipe.query()                               │  │
│  │  - Displays answer + sources                          │  │
│  │  - Displays timing panel (if show_timing=True)        │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
        ┌──────────────────┴─────────────────┐
        ▼                                    ▼
┌─────────────────────┐          ┌─────────────────────────┐
│  common/recipe.py   │          │ common/resource_mgr.py  │
│                     │          │       (NEW)             │
│  query() method:    │          │                         │
│  - Track search time│          │ - create_client()       │
│  - Track LLM time   │          │ - add_resource()        │
│  - Return timings   │          │                         │
└─────────────────────┘          └─────────────────────────┘
                                            ▲
                                            │
                                    ┌───────┴────────┐
                                    │    add.py      │
                                    │  (refactored)  │
                                    └────────────────┘
```

### Key Components

1. **Timing Instrumentation** (`common/recipe.py`)
   - Uses `time.perf_counter()` for high-precision timing
   - Tracks three metrics: search, LLM, total
   - Returns timing data in result dictionary

2. **Resource Manager** (`common/resource_manager.py`)
   - Extracts core logic from `add.py`
   - Reusable functions for client creation and resource addition
   - Consistent error handling and user feedback

3. **Command Handlers** (`chatmem.py`)
   - Extends existing command system
   - Integrates timing display
   - Reuses existing OpenViking client for resource addition

## Detailed Design

### 1. Timing Implementation

#### Recipe.query() Modifications

Add timing instrumentation to track three phases:

```python
def query(self, user_query: str, ...) -> Dict[str, Any]:
    import time

    # Track total time
    start_total = time.perf_counter()

    # Step 1: Search (timed)
    start_search = time.perf_counter()
    search_results = self.search(user_query, ...)
    search_time = time.perf_counter() - start_search

    # Step 2: Build context (not separately timed)
    context_text = ...
    messages = ...

    # Step 3: LLM call (timed)
    start_llm = time.perf_counter()
    answer = self.call_llm(messages, ...)
    llm_time = time.perf_counter() - start_llm

    total_time = time.perf_counter() - start_total

    return {
        "answer": answer,
        "context": search_results,
        "query": user_query,
        "prompt": current_prompt,
        "timings": {  # NEW
            "search_time": search_time,
            "llm_time": llm_time,
            "total_time": total_time
        }
    }
```

#### ChatREPL.ask_question() Modifications

Add optional `show_timing` parameter:

```python
def ask_question(self, question: str, show_timing: bool = False) -> bool:
    # ... existing code to call recipe.query() ...

    # Display answer and sources (existing)
    console.print(Panel(answer_text, ...))
    console.print(sources_table)

    # Display timing panel (NEW)
    if show_timing and "timings" in result:
        timings = result["timings"]

        timing_table = Table(show_header=False, box=None)
        timing_table.add_column("Metric", style="cyan")
        timing_table.add_column("Time", style="bold green", justify="right")

        timing_table.add_row("Search", f"{timings['search_time']:.3f}s")
        timing_table.add_row("LLM Generation", f"{timings['llm_time']:.3f}s")
        timing_table.add_row("Total", f"{timings['total_time']:.3f}s")

        console.print(Panel(
            timing_table,
            title="⏱️  Performance",
            style="bold blue",
            padding=(0, 1),
            width=PANEL_WIDTH
        ))

    return True
```

#### Command Handler

Add `/time` command to `handle_command()`:

```python
def handle_command(self, cmd: str) -> bool:
    # ... existing commands ...

    elif cmd.startswith("/time"):
        # Extract question from command
        question = cmd[5:].strip()  # Remove "/time" prefix

        if not question:
            console.print("Usage: /time <your question>", style="yellow")
            console.print("Example: /time what is prompt engineering?", style="dim")
            return False

        # Ask question with timing enabled
        self.ask_question(question, show_timing=True)
        return False
```

### 2. Resource Manager Implementation

#### New File: common/resource_manager.py

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

#### Refactor add.py

Simplify `add.py` to use the shared module:

```python
#!/usr/bin/env python3
"""
Add Resource - CLI tool to add documents to OpenViking database
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.resource_manager import create_client, add_resource


def main():
    parser = argparse.ArgumentParser(
        description="Add documents, PDFs, or URLs to OpenViking database",
        # ... existing epilog ...
    )

    parser.add_argument("resource", type=str, help="Path to file/directory or URL")
    parser.add_argument("--config", type=str, default="./ov.conf")
    parser.add_argument("--data", type=str, default="./data")

    args = parser.parse_args()

    # Expand user paths
    resource_path = (
        str(Path(args.resource).expanduser())
        if not args.resource.startswith("http")
        else args.resource
    )

    # Create client and add resource
    try:
        client = create_client(args.config, args.data)
        success = add_resource(client, resource_path)
        client.close()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
```

#### Add Command Handler in chatmem.py

```python
def handle_command(self, cmd: str) -> bool:
    # ... existing commands ...

    elif cmd.startswith("/add_resource"):
        # Extract resource path from command
        resource_path = cmd[13:].strip()  # Remove "/add_resource" prefix

        if not resource_path:
            console.print("Usage: /add_resource <path/to/file or URL>", style="yellow")
            console.print("Examples:", style="dim")
            console.print("  /add_resource ~/Downloads/paper.pdf", style="dim")
            console.print("  /add_resource https://example.com/doc.md", style="dim")
            return False

        # Expand user path
        if not resource_path.startswith("http"):
            resource_path = str(Path(resource_path).expanduser())

        # Import resource manager
        from common.resource_manager import add_resource

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
            console.print("💡 You can now ask questions about this resource!", style="dim")

        console.print()
        return False
```

## Error Handling

### /time Command Errors

| Error | Handling |
|-------|----------|
| Empty question | Show usage message with example |
| Query fails | Show error panel, no timing displayed |
| Timing data missing | Show answer without timing (graceful degradation) |

### /add_resource Command Errors

| Error | Handling |
|-------|----------|
| No path provided | Show usage with examples |
| File not found | Show error panel with full path |
| Invalid URL | Show error from underlying library |
| Processing fails | Show error with details |
| Already added | OpenViking handles deduplication (no error) |

## Testing Strategy

### Manual Testing

**Test /time command:**
```bash
# Start chat
uv run chatmem.py

# Test normal query (no timing)
You: what is RAG?

# Test with timing
You: /time what is RAG?

# Test empty question
You: /time

# Test with complex question
You: /time explain chain of thought prompting in detail
```

**Test /add_resource command:**
```bash
# Start chat
uv run chatmem.py

# Test adding local file
You: /add_resource ~/Downloads/paper.pdf

# Test adding URL
You: /add_resource https://raw.githubusercontent.com/example/README.md

# Test file not found
You: /add_resource /nonexistent/file.pdf

# Test empty path
You: /add_resource

# Verify resource is searchable
You: what does the paper say?
```

### Edge Cases

1. **Large files** - Ensure spinner shows during long processing
2. **Network failures** - URL downloads should show clear error
3. **Concurrent adds** - Multiple `/add_resource` calls in sequence
4. **Timing precision** - Very fast queries (< 0.1s) should still show accurate timing

## Implementation Plan

### Phase 1: Timing Feature
1. Add timing instrumentation to `Recipe.query()`
2. Add timing display to `ChatREPL.ask_question()`
3. Add `/time` command handler
4. Test with various queries

### Phase 2: Resource Manager
1. Create `common/resource_manager.py` with shared functions
2. Refactor `add.py` to use shared module
3. Test standalone `add.py` still works

### Phase 3: Chat Integration
1. Add `/add_resource` command handler to `chatmem.py`
2. Update help text to include new commands
3. Test in-chat resource addition
4. Test that added resources are immediately searchable

## Files Modified

| File | Changes | Lines Changed |
|------|---------|---------------|
| `common/resource_manager.py` | NEW | ~80 lines |
| `common/recipe.py` | Add timing instrumentation | ~15 lines |
| `chatmem.py` | Add command handlers, timing display | ~60 lines |
| `add.py` | Refactor to use shared module | ~30 lines (simplified) |

**Total:** ~185 lines of new/modified code

## Future Enhancements

- Add `/time toggle` to enable persistent timing display
- Color-code timing values (green < 1s, yellow < 3s, red >= 3s)
- Add `/list_resources` command to show all indexed resources
- Add `/remove_resource` command to remove resources
- Export timing data to CSV for performance analysis

## Appendix: User Experience Examples

### Example 1: Using /time

```
You: /time what is retrieval augmented generation?

✅ Roger That
┌────────────────────────────────────────────────────┐
│ what is retrieval augmented generation?            │
└────────────────────────────────────────────────────┘

🍔 Check This Out
┌────────────────────────────────────────────────────┐
│ Retrieval Augmented Generation (RAG) is a         │
│ technique that combines information retrieval...   │
└────────────────────────────────────────────────────┘

📚 Sources (3 documents)
┌───┬──────────────────┬───────────┐
│ # │ File             │ Relevance │
├───┼──────────────────┼───────────┤
│ 1 │ rag_intro.md     │  0.8234   │
│ 2 │ llm_patterns.md  │  0.7456   │
│ 3 │ architecture.md  │  0.6892   │
└───┴──────────────────┴───────────┘

⏱️  Performance
┌─────────────────┬─────────┐
│ Search          │  0.234s │
│ LLM Generation  │  1.567s │
│ Total           │  1.801s │
└─────────────────┴─────────┘

You:
```

### Example 2: Using /add_resource

```
You: /add_resource ~/Downloads/transformer_paper.pdf

📂 Adding resource: /Users/user/Downloads/transformer_paper.pdf
✓ Resource added: file:///transformer_paper.pdf
⏳ Processing and indexing...
✓ Processing complete!
🎉 Resource is now searchable!

💡 You can now ask questions about this resource!

You: what is the attention mechanism in the paper?

... (answer based on newly added paper) ...
```
