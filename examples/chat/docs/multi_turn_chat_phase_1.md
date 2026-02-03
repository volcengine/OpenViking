# Agent Handoff: Multi-turn Chat Implementation (Phase 1)

**Entry Point for Next Agent**

## Current Status

**Location:** `/Users/bytedance/code/OpenViking/.worktrees/chat-examples`
**Branch:** `examples/chat`
**Working Directory:** `examples/chat/`

### ✅ Completed Tasks (2/9)

**Task 1: Directory Structure ✅**
- Commit: 17269b6, e7030a3
- Created examples/chat/ with pyproject.toml, .gitignore
- Symlinks: recipe.py, boring_logging_config.py
- Fixed: Removed broken data symlink (runtime artifact)

**Task 2: ChatSession Class ✅**
- Commit: 87d01ed
- File: examples/chat/chat.py
- Implemented: ChatSession with add_turn(), clear(), get_turn_count()
- Tests: Manual tests passing
- Reviews: Spec compliant, functionally approved

### 🔄 Remaining Tasks (7/9)

**Task 3:** Implement basic REPL structure
**Task 4:** Implement welcome banner and help
**Task 5:** Implement question/answer display
**Task 6:** Implement main REPL loop
**Task 7:** Add README documentation
**Task 8:** Manual testing and verification
**Task 9:** Final integration and handoff prep

## Your Mission

Continue the **subagent-driven development** process using the `superpowers:subagent-driven-development` skill to complete Tasks 3-9.

### Instructions for Next Agent

1. **Read the full implementation plan:**
   - File: `docs/plans/2026-02-02-chat-implementation.md`
   - Note: The file is truncated at 178 lines - use the detailed task descriptions below

2. **Use the TodoWrite task list:**
   - Tasks 1-2 are already marked complete
   - Tasks 3-9 are pending - update status as you work

3. **Follow subagent-driven development process:**
   - For each task (3-9):
     a. Mark task as in_progress
     b. Dispatch implementer subagent with full task context
     c. Answer any questions from implementer
     d. Dispatch spec compliance reviewer
     e. If issues found: implementer fixes, re-review
     f. Dispatch code quality reviewer
     g. If issues found: implementer fixes, re-review
     h. Mark task as completed
   - After all tasks: dispatch final code reviewer
   - Use `superpowers:finishing-a-development-branch`

4. **Maintain quality gates:**
   - Two-stage review: spec compliance THEN code quality
   - Review loops until approved
   - Fresh subagent per task

---

## Detailed Task Specifications

### Task 3: Implement Basic REPL Structure

**Files:**
- Modify: `examples/chat/chat.py`

**Requirements:**

1. Add imports at top:
```python
import sys
import signal
from recipe import Recipe
from rich.live import Live
from rich.spinner import Spinner
import threading

PANEL_WIDTH = 78
```

2. Add ChatREPL class:
```python
class ChatREPL:
    """Interactive chat REPL"""

    def __init__(
        self,
        config_path: str = "./ov.conf",
        data_path: str = "./data",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        top_k: int = 5,
        score_threshold: float = 0.2
    ):
        """Initialize chat REPL"""
        self.config_path = config_path
        self.data_path = data_path
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_k = top_k
        self.score_threshold = score_threshold

        self.recipe: Recipe = None
        self.session = ChatSession()
        self.should_exit = False

        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle Ctrl-C gracefully"""
        console.print("\n")
        console.print(Panel("👋 Goodbye!", style="bold yellow", padding=(0, 1), width=PANEL_WIDTH))
        self.should_exit = True
        sys.exit(0)

    def run(self):
        """Main REPL loop"""
        pass  # To be implemented in Task 6
```

**Test:**
```bash
python3 -c "
from chat import ChatREPL
repl = ChatREPL()
assert repl.session.get_turn_count() == 0
print('ChatREPL init: OK')
"
```

**Commit:** `"feat(chat): add ChatREPL class skeleton with signal handling"`

---

### Task 4: Implement Welcome Banner and Help

**Files:**
- Modify: `examples/chat/chat.py`

**Requirements:**

Add these methods to ChatREPL class:

```python
def _show_welcome(self):
    """Display welcome banner"""
    console.clear()
    welcome_text = Text()
    welcome_text.append("🚀 OpenViking Chat\n\n", style="bold cyan")
    welcome_text.append("Multi-turn conversation powered by RAG\n", style="white")
    welcome_text.append("Type ", style="dim")
    welcome_text.append("/help", style="bold yellow")
    welcome_text.append(" for commands or ", style="dim")
    welcome_text.append("/exit", style="bold yellow")
    welcome_text.append(" to quit", style="dim")

    console.print(Panel(
        welcome_text,
        style="bold",
        padding=(1, 2),
        width=PANEL_WIDTH
    ))
    console.print()

def _show_help(self):
    """Display help message"""
    help_text = Text()
    help_text.append("Available Commands:\n\n", style="bold cyan")
    help_text.append("/help", style="bold yellow")
    help_text.append("   - Show this help message\n", style="white")
    help_text.append("/clear", style="bold yellow")
    help_text.append("  - Clear screen (keeps history)\n", style="white")
    help_text.append("/exit", style="bold yellow")
    help_text.append("   - Exit chat\n", style="white")
    help_text.append("/quit", style="bold yellow")
    help_text.append("   - Exit chat\n", style="white")
    help_text.append("\nKeyboard Shortcuts:\n\n", style="bold cyan")
    help_text.append("Ctrl-C", style="bold yellow")
    help_text.append("  - Exit gracefully\n", style="white")
    help_text.append("Ctrl-D", style="bold yellow")
    help_text.append("  - Exit\n", style="white")
    help_text.append("↑/↓", style="bold yellow")
    help_text.append("     - Navigate input history", style="white")

    console.print(Panel(
        help_text,
        title="Help",
        style="bold green",
        padding=(1, 2),
        width=PANEL_WIDTH
    ))
    console.print()

def handle_command(self, cmd: str) -> bool:
    """
    Handle slash commands

    Args:
        cmd: Command string (e.g., "/help")

    Returns:
        True if should exit, False otherwise
    """
    cmd = cmd.strip().lower()

    if cmd in ["/exit", "/quit"]:
        console.print(Panel(
            "👋 Goodbye!",
            style="bold yellow",
            padding=(0, 1),
            width=PANEL_WIDTH
        ))
        return True
    elif cmd == "/help":
        self._show_help()
    elif cmd == "/clear":
        console.clear()
        self._show_welcome()
    else:
        console.print(f"Unknown command: {cmd}", style="red")
        console.print("Type /help for available commands", style="dim")
        console.print()

    return False
```

**Test:**
```bash
python3 -c "
from chat import ChatREPL
repl = ChatREPL()
assert repl.handle_command('/help') == False
assert repl.handle_command('/clear') == False
assert repl.handle_command('/exit') == True
print('Commands: OK')
"
```

**Commit:** `"feat(chat): implement welcome banner, help, and command handling"`

---

### Task 5: Implement Question/Answer Display

**Files:**
- Modify: `examples/chat/chat.py`

**Requirements:**

1. Add spinner helper before ChatSession class:

```python
def show_loading_with_spinner(message: str, target_func, *args, **kwargs):
    """Show a loading spinner while a function executes"""
    spinner = Spinner("dots", text=message)
    result = None
    exception = None

    def run_target():
        nonlocal result, exception
        try:
            result = target_func(*args, **kwargs)
        except Exception as e:
            exception = e

    thread = threading.Thread(target=run_target)
    thread.start()

    with Live(spinner, console=console, refresh_per_second=10, transient=True):
        thread.join()

    console.print()

    if exception:
        raise exception

    return result
```

2. Add ask_question method to ChatREPL:

```python
def ask_question(self, question: str) -> bool:
    """Ask a question and display the answer"""
    try:
        # Query with loading spinner
        result = show_loading_with_spinner(
            "Thinking...",
            self.recipe.query,
            user_query=question,
            search_top_k=self.top_k,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            score_threshold=self.score_threshold
        )

        # Display answer
        answer_text = Text(result['answer'], style="white")
        console.print(Panel(
            answer_text,
            title="💡 Answer",
            style="bold bright_cyan",
            padding=(1, 1),
            width=PANEL_WIDTH
        ))
        console.print()

        # Display sources
        if result['context']:
            from rich.table import Table
            from rich import box

            sources_table = Table(
                title=f"📚 Sources ({len(result['context'])} documents)",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold magenta",
                title_style="bold magenta"
            )
            sources_table.add_column("#", style="cyan", width=4)
            sources_table.add_column("File", style="bold white")
            sources_table.add_column("Relevance", style="green", justify="right")

            for i, ctx in enumerate(result['context'], 1):
                uri_parts = ctx['uri'].split('/')
                filename = uri_parts[-1] if uri_parts else ctx['uri']
                score_text = Text(f"{ctx['score']:.4f}", style="bold green")
                sources_table.add_row(str(i), filename, score_text)

            console.print(sources_table)
        console.print()

        # Add to history
        self.session.add_turn(question, result['answer'], result['context'])

        return True

    except Exception as e:
        console.print(Panel(
            f"❌ Error: {e}",
            style="bold red",
            padding=(0, 1),
            width=PANEL_WIDTH
        ))
        console.print()
        return False
```

**Commit:** `"feat(chat): implement question/answer display with sources"`

---

### Task 6: Implement Main REPL Loop

**Files:**
- Modify: `examples/chat/chat.py`

**Requirements:**

1. Replace the `pass` in `ChatREPL.run()` with:

```python
def run(self):
    """Main REPL loop"""
    # Initialize recipe
    try:
        self.recipe = Recipe(
            config_path=self.config_path,
            data_path=self.data_path
        )
    except Exception as e:
        console.print(Panel(
            f"❌ Error initializing: {e}",
            style="bold red",
            padding=(0, 1)
        ))
        return

    # Show welcome
    self._show_welcome()

    # Enable readline for input history
    try:
        import readline
    except ImportError:
        pass

    # Main loop
    try:
        while not self.should_exit:
            try:
                # Get user input
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()

                # Skip empty input
                if not user_input:
                    continue

                # Handle commands
                if user_input.startswith('/'):
                    if self.handle_command(user_input):
                        break
                    continue

                # Ask question
                self.ask_question(user_input)

            except EOFError:
                # Ctrl-D pressed
                console.print("\n")
                console.print(Panel(
                    "👋 Goodbye!",
                    style="bold yellow",
                    padding=(0, 1),
                    width=PANEL_WIDTH
                ))
                break

    finally:
        # Cleanup
        if self.recipe:
            self.recipe.close()
```

2. Add main entry point at end of file:

```python
def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Multi-turn chat with OpenViking RAG",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Start chat with default settings
  uv run chat.py

  # Adjust creativity
  uv run chat.py --temperature 0.9

  # Use more context
  uv run chat.py --top-k 10

  # Enable debug logging
  OV_DEBUG=1 uv run chat.py
        """
    )

    parser.add_argument('--config', type=str, default='./ov.conf', help='Path to config file')
    parser.add_argument('--data', type=str, default='./data', help='Path to data directory')
    parser.add_argument('--top-k', type=int, default=5, help='Number of search results')
    parser.add_argument('--temperature', type=float, default=0.7, help='LLM temperature 0.0-1.0')
    parser.add_argument('--max-tokens', type=int, default=2048, help='Max tokens to generate')
    parser.add_argument('--score-threshold', type=float, default=0.2, help='Min relevance score')

    args = parser.parse_args()

    # Validate arguments
    if not 0.0 <= args.temperature <= 1.0:
        console.print("❌ Temperature must be between 0.0 and 1.0", style="bold red")
        sys.exit(1)

    if args.top_k < 1:
        console.print("❌ top-k must be at least 1", style="bold red")
        sys.exit(1)

    if not 0.0 <= args.score_threshold <= 1.0:
        console.print("❌ score-threshold must be between 0.0 and 1.0", style="bold red")
        sys.exit(1)

    # Run chat
    repl = ChatREPL(
        config_path=args.config,
        data_path=args.data,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        top_k=args.top_k,
        score_threshold=args.score_threshold
    )

    repl.run()


if __name__ == "__main__":
    main()
```

**Test:**
```bash
# Interactive test - manually verify:
# Copy config: cp ../query/ov.conf ./ov.conf
# Start: uv run chat.py
# Test: ask question, /help, /exit
```

**Commit:** `"feat(chat): implement main REPL loop with readline support"`

---

### Task 7: Add README Documentation

**Files:**
- Create: `examples/chat/README.md`

**Content:** Create comprehensive README with:
- Quick start (setup, config, start chat)
- Features (multi-turn, sources, history, rich UI)
- Usage (basic chat, commands, options)
- Commands (/help, /clear, /exit, /quit, Ctrl-C, Ctrl-D)
- Configuration (ov.conf structure)
- Architecture (ChatSession, ChatREPL, Recipe)
- Tips and troubleshooting

**Commit:** `"docs(chat): add comprehensive README with usage examples"`

---

### Task 8: Manual Testing and Verification

**Requirements:**

1. Verify directory structure: `ls -la examples/chat`
2. Test functionality:
   - Welcome banner displays
   - `/help` command
   - `/clear` command
   - Ask question → answer + sources
   - Follow-up question
   - Arrow keys for history
   - `/exit`, Ctrl-C, Ctrl-D
3. Test error handling (missing config)
4. Test command line options (`--help`, `--temperature`)

**Deliverable:** Create `examples/chat/TESTING.md` with checklist and results

**Commit:** `"test(chat): add manual test results"`

---

### Task 9: Final Integration and Handoff Prep

**Files:**
- Create: `examples/chat/HANDOFF.md`

**Content:**
- Phase 1 summary (what works)
- Architecture overview
- Phase 2 requirements (session persistence with OpenViking Session API)
- Technical notes for Session API integration
- Implementation strategy for Phase 2
- Success criteria
- Files to reference

**Commits:**
1. `"docs(chat): add Phase 2 handoff document"`
2. `"feat(chat): Phase 1 complete - multi-turn chat interface"` (final summary commit)

---

## Important Notes

### YAGNI Principle
- Phase 1 is simple, in-memory only
- Don't over-engineer
- Phase 2 will add OpenViking Session API (different architecture)
- Keep code focused on current requirements

### Data Directory
- `../query/data` may not exist yet - that's OK
- Data is created at runtime when users add documents
- Recipe will use `./data` path (can be configured)

### Review Standards
- **Spec compliance:** Must match specification exactly
- **Code quality:** Functional, readable, maintainable
- **Balance:** Don't over-engineer for Phase 1, but maintain quality

### After All Tasks Complete
1. Run final code reviewer for entire implementation
2. Use `superpowers:finishing-a-development-branch` skill
3. Create PR or merge as appropriate

---

## Task List Reference

Use `TaskUpdate` to track progress:
- Task #1: ✅ Complete (Directory structure)
- Task #2: ✅ Complete (ChatSession class)
- Task #3: 🔄 Implement basic REPL structure
- Task #4: 🔄 Implement welcome banner and help
- Task #5: 🔄 Implement question/answer display
- Task #6: 🔄 Implement main REPL loop
- Task #7: 🔄 Add README documentation
- Task #8: 🔄 Manual testing and verification
- Task #9: 🔄 Final integration and handoff prep

---

## Quick Start for Next Agent

```
1. Read this file completely
2. Navigate to: cd /Users/bytedance/code/OpenViking/.worktrees/chat-examples/examples/chat
3. Verify current state: git log --oneline -3
4. Start Task 3 with subagent-driven-development approach
5. Follow the process for each task 3-9
6. Complete with finishing-a-development-branch
```

Good luck! 🚀
