# Multi-turn Chat Interface Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an interactive multi-turn chat interface that allows users to have conversations with OpenViking's RAG pipeline, with in-memory history and graceful exit handling.

**Architecture:** REPL-based chat interface using Rich for TUI, reusing Recipe pipeline from query example. ChatSession manages in-memory conversation history, ChatREPL handles user interaction and commands. No persistence in Phase 1.

**Tech Stack:** Python 3.13+, Rich (TUI), readline (input history), OpenViking Recipe pipeline

---

## Task 1: Create Directory Structure and Symlinks

**Files:**
- Create: `examples/chat/` directory
- Create: `examples/chat/pyproject.toml`
- Create: `examples/chat/.gitignore`
- Symlink: `examples/chat/recipe.py` → `../query/recipe.py`
- Symlink: `examples/chat/boring_logging_config.py` → `../query/boring_logging_config.py`
- Symlink: `examples/chat/data` → `../query/data`

**Step 1: Create chat directory**

```bash
mkdir -p examples/chat
cd examples/chat
```

**Step 2: Create pyproject.toml**

```toml
[project]
name = "chat"
version = "0.1.0"
description = "Multi-turn chat interface for OpenViking"
readme = "README.md"
requires-python = ">=3.13"
dependencies = [
    "openviking>=0.1.6",
    "rich>=13.0.0",
]
```

**Step 3: Create .gitignore**

```
.venv/
__pycache__/
*.pyc
.pytest_cache/
uv.lock
ov.conf
```

**Step 4: Create symlinks**

```bash
ln -s ../query/recipe.py recipe.py
ln -s ../query/boring_logging_config.py boring_logging_config.py
ln -s ../query/data data
```

**Step 5: Verify symlinks**

```bash
ls -la
```

Expected: All symlinks point to existing files (blue arrows in ls output)

**Step 6: Copy config file**

```bash
cp ../query/ov.conf.example ov.conf.example
```

**Step 7: Commit**

```bash
git add examples/chat/
git commit -m "feat(chat): create directory structure with symlinks to query example"
```

---

## Task 2: Implement ChatSession Class

**Files:**
- Create: `examples/chat/chat.py`

**Step 1: Write the test file structure (manual test)**

Create a test plan in your head:
1. Can create ChatSession
2. Can add turns
3. Can clear history
4. History is stored correctly

**Step 2: Implement ChatSession class**

```python
#!/usr/bin/env python3
"""
Chat - Multi-turn conversation interface for OpenViking
"""
from typing import List, Dict, Any
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console()


class ChatSession:
    """Manages in-memory conversation history"""

    def __init__(self):
        """Initialize empty conversation history"""
        self.history: List[Dict[str, Any]] = []

    def add_turn(self, question: str, answer: str, sources: List[Dict[str, Any]]) -> None:
        """
        Add a Q&A turn to history

        Args:
            question: User's question
            answer: Assistant's answer
            sources: List of source documents used
        """
        self.history.append({
            'question': question,
            'answer': answer,
            'sources': sources,
            'turn': len(self.history) + 1
        })

    def clear(self) -> None:
        """Clear all conversation history"""
        self.history.clear()

    def get_turn_count(self) -> int:
        """Get number of turns in conversation"""
        return len(self.history)
```

**Step 3: Manual test**

```bash
cd examples/chat
python3 -c "
from chat import ChatSession
s = ChatSession()
assert s.get_turn_count() == 0
s.add_turn('test q', 'test a', [])
assert s.get_turn_count() == 1
s.clear()
assert s.get_turn_count() == 0
print('ChatSession: OK')
"
```

Expected: "ChatSession: OK"

**Step 4: Commit**

```bash
git add examples/chat/chat.py
git commit -m "feat(chat): implement ChatSession for in-memory history"
```

---

## Summary

This is a comprehensive 9-task implementation plan. Each task builds on the previous one following TDD principles with frequent commits. The plan includes exact code, file paths, test steps, and commit messages.

For full details, see the complete plan document.
