"""Utility functions for vikingbot."""

from pathlib import Path
from datetime import datetime


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists, creating it if necessary."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_data_path() -> Path:
    """Get the vikingbot data directory (~/.vikingbot)."""
    return ensure_dir(Path.home() / ".vikingbot")


def get_source_workspace_path() -> Path:
    """Get the source workspace path from the codebase."""
    return Path(__file__).parent.parent.parent / "workspace"


def get_workspace_path(workspace: str | None = None, ensure_exists: bool = True) -> Path:
    """
    Get the workspace path.

    Args:
        workspace: Optional workspace path. Defaults to ~/.vikingbot/workspace/shared.
        ensure_exists: If True, ensure the directory exists (creates it if necessary.

    Returns:
        Expanded workspace path.
    """
    if workspace:
        path = Path(workspace).expanduser()
    else:
        path = Path.home() / ".vikingbot" / "workspace" / "shared"

    if ensure_exists:
        ensure_workspace_templates(path)
        return ensure_dir(path)
    return path


def ensure_workspace_templates(workspace: Path) -> None:
    import shutil
    from vikingbot.agent.skills import BUILTIN_SKILLS_DIR

    # Ensure workspace directory exists first
    ensure_dir(workspace)

    # Check if workspace has any of the bootstrap files
    bootstrap_files = ["AGENTS.md", "SOUL.md", "USER.md", "TOOLS.md", "IDENTITY.md"]
    has_any_file = any((workspace / filename).exists() for filename in bootstrap_files)

    if not has_any_file:
        # Workspace is empty, copy templates from source
        source_dir = Path(__file__).parent.parent.parent / "workspace"

        if not source_dir.exists():
            # Fallback: create minimal templates
            _create_minimal_workspace_templates(workspace)
        else:
            # Copy all files and directories from source workspace
            for item in source_dir.iterdir():
                src = source_dir / item.name
                dst = workspace / item.name

                if src.is_dir():
                    if src.name == "memory":
                        # Ensure memory directory exists
                        dst.mkdir(exist_ok=True)
                        # Copy memory files
                        for mem_file in src.iterdir():
                            if mem_file.is_file():
                                shutil.copy2(mem_file, dst / mem_file.name)
                    else:
                        # Copy other directories
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    # Copy individual files
                    if not dst.exists():
                        shutil.copy2(src, dst)

            # Ensure skills directory exists (for custom user skills)
            skills_dir = workspace / "skills"
            skills_dir.mkdir(exist_ok=True)

            # Copy built-in skills to workspace skills directory
            if BUILTIN_SKILLS_DIR.exists() and BUILTIN_SKILLS_DIR.is_dir():
                for skill_dir in BUILTIN_SKILLS_DIR.iterdir():
                    if skill_dir.is_dir() and skill_dir.name != "README.md":
                        dst_skill_dir = skills_dir / skill_dir.name
                        if not dst_skill_dir.exists():
                            shutil.copytree(skill_dir, dst_skill_dir)

    # Always ensure memory and skills directories exist
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)

    # Create default memory files if they don't exist
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")

    history_file = memory_dir / "HISTORY.md"
    if not history_file.exists():
        history_file.write_text("")

    skills_dir = workspace / "skills"
    skills_dir.mkdir(exist_ok=True)


def ensure_session_workspace(workspace_path: Path) -> Path:
    if workspace_path.exists() and workspace_path.is_dir():
        return workspace_path

    ensure_workspace_templates(workspace_path)
    return workspace_path


def _create_minimal_workspace_templates(workspace: Path) -> None:
    """Create minimal workspace templates as fallback."""
    templates = {
        "AGENTS.md": """# Agent Instructions

You are a helpful AI assistant. Be concise, accurate, and friendly.

## Guidelines

- Always explain what you're doing before taking actions
- Ask for clarification when the request is ambiguous
- Use tools to help accomplish tasks
- Remember important information in openviking, and memory/MEMORY.md; past events are logged in openviking, and memory/HISTORY.md
""",
        "SOUL.md": """# Soul

I am vikingbot, a lightweight AI assistant.

## Personality

- Helpful and friendly
- Concise and to the point
- Curious and eager to learn

## Values

- Accuracy over speed
- User privacy and safety
- Transparency in actions
""",
        "USER.md": """# User

Information about the user goes here.

## Preferences

- Communication style: (casual/formal)
- Timezone: (your timezone)
- Language: (your preferred language)
""",
    }

    for filename, content in templates.items():
        file_path = workspace / filename
        if not file_path.exists():
            file_path.write_text(content)

    # Create memory directory and MEMORY.md
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_file = memory_dir / "MEMORY.md"
    if not memory_file.exists():
        memory_file.write_text("""# Long-term Memory

This file stores important information that should persist across sessions.

## User Information

(Important facts about the user)

## Preferences

(User preferences learned over time)

## Important Notes

(Things to remember)
""")

    history_file = memory_dir / "HISTORY.md"
    if not history_file.exists():
        history_file.write_text("")

    # Create skills directory for custom user skills
    skills_dir = workspace / "skills"
    skills_dir.mkdir(exist_ok=True)


def get_sessions_path() -> Path:
    """Get the sessions storage directory."""
    return ensure_dir(get_data_path() / "sessions")


def get_skills_path(workspace: Path | None = None) -> Path:
    """Get the skills directory within the workspace."""
    ws = workspace or get_workspace_path()
    return ensure_dir(ws / "skills")


def cal_str_tokens(text: str, text_type: str = "mixed") -> int:
    char_length = len(text)
    if text_type == "en":
        token_count = char_length / 4.5  # 1 token ≈ 4.5个英文字符
    elif text_type == "zh":
        token_count = char_length / 1.1  # 1 token ≈ 1.1个中文字符
    else:  # mixed
        token_count = char_length / 2.5  # 混合文本折中值
    return int(token_count) + 1


def timestamp() -> str:
    """Get current timestamp in ISO format."""
    return datetime.now().isoformat()


def truncate_string(s: str, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate a string to max length, adding suffix if truncated."""
    if len(s) <= max_len:
        return s
    return s[: max_len - len(suffix)] + suffix
