"""File system tools: read, write, edit."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

from vikingbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from vikingbot.sandbox.manager import SandboxManager


def _resolve_path(path: str, allowed_dir: Path | None = None) -> Path:
    """Resolve path and optionally enforce directory restriction."""
    resolved = Path(path).expanduser().resolve()
    if allowed_dir and not str(resolved).startswith(str(allowed_dir.resolve())):
        raise PermissionError(f"Path {path} is outside allowed directory {allowed_dir}")
    return resolved


class ReadFileTool(Tool):
    """Tool to read file contents."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._session_key: str | None = None

    def set_session_key(self, session_key: str) -> None:
        self._session_key = session_key

    @property
    def name(self) -> str:
        return "read_file"
    
    @property
    def description(self) -> str:
        return "Read the contents of a file at the given path."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to read"
                }
            },
            "required": ["path"]
        }
    
    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            if self._sandbox_manager and self._session_key:
                sandbox = await self._sandbox_manager.get_sandbox(self._session_key)
                input_path = Path(path)

                if input_path.is_absolute():
                    if path == "/":
                        sandbox_path = sandbox.workspace
                    else:
                        resolved = input_path.resolve()
                        sandbox_resolved = sandbox.workspace.resolve()
                        if not str(resolved).startswith(str(sandbox_resolved)):
                            return f"Error: Absolute path outside sandbox: {path}"
                        sandbox_path = resolved
                else:
                    sandbox_path = sandbox.workspace / path

                if not sandbox_path.exists():
                    return f"Error: File not found: {path}"
                if not sandbox_path.is_file():
                    return f"Error: Not a file: {path}"
                content = sandbox_path.read_text(encoding="utf-8")
                return content

            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"
            if not file_path.is_file():
                return f"Error: Not a file: {path}"

            content = file_path.read_text(encoding="utf-8")
            return content
        except Exception as e:
            return f"Error reading file: {str(e)}"
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._session_key: str | None = None

    def set_session_key(self, session_key: str) -> None:
        self._session_key = session_key

    @property
    def name(self) -> str:
        return "write_file"
    
    @property
    def description(self) -> str:
        return "Write content to a file at the given path. Creates parent directories if needed."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to write to"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }
    
    async def execute(self, path: str, content: str, **kwargs: Any) -> str:
        try:
            if self._sandbox_manager and self._session_key:
                sandbox = await self._sandbox_manager.get_sandbox(self._session_key)
                input_path = Path(path)

                if input_path.is_absolute():
                    resolved = input_path.resolve()
                    sandbox_resolved = sandbox.workspace.resolve()
                    if not str(resolved).startswith(str(sandbox_resolved)):
                        return f"Error: Absolute path outside sandbox: {path}"

                sandbox_path = sandbox.workspace / path
                sandbox_path.parent.mkdir(parents=True, exist_ok=True)
                sandbox_path.write_text(content, encoding="utf-8")
                return f"Successfully wrote {len(content)} bytes to {path}"

            file_path = _resolve_path(path, self._allowed)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            return f"Successfully wrote {len(content)} bytes to {path}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._session_key: str | None = None

    def set_session_key(self, session_key: str) -> None:
        self._session_key = session_key

    @property
    def name(self) -> str:
        return "edit_file"
    
    @property
    def description(self) -> str:
        return "Edit a file by replacing old_text with new_text. The old_text must exist exactly in the file."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The file path to edit"
                },
                "old_text": {
                    "type": "string",
                    "description": "The exact text to find and replace"
                },
                "new_text": {
                    "type": "string",
                    "description": "The text to replace with"
                }
            },
            "required": ["path", "old_text", "new_text"]
        }
    
    async def execute(self, path: str, old_text: str, new_text: str, **kwargs: Any) -> str:
        try:
            if self._sandbox_manager and self._session_key:
                sandbox = await self._sandbox_manager.get_sandbox(self._session_key)
                sandbox_path = sandbox.workspace / path

                if sandbox_path.is_absolute():
                    return f"Error: Absolute paths are not allowed in sandbox: {path}"

                if not sandbox_path.exists():
                    return f"Error: File not found: {path}"

                content = sandbox_path.read_text(encoding="utf-8")

                if old_text not in content:
                    return f"Error: old_text not found in file. Make sure it matches exactly."

                count = content.count(old_text)
                if count > 1:
                    return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

                new_content = content.replace(old_text, new_text, 1)
                sandbox_path.write_text(new_content, encoding="utf-8")

                return f"Successfully edited {path}"

            file_path = _resolve_path(path, self._allowed_dir)
            if not file_path.exists():
                return f"Error: File not found: {path}"

            content = file_path.read_text(encoding="utf-8")

            if old_text not in content:
                return f"Error: old_text not found in file. Make sure it matches exactly."

            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            file_path.write_text(new_content, encoding="utf-8")

            return f"Successfully edited {path}"
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

    def __init__(
        self,
        allowed_dir: Path | None = None,
        sandbox_manager: "SandboxManager | None" = None,
    ):
        self._allowed_dir = allowed_dir
        self._sandbox_manager = sandbox_manager
        self._session_key: str | None = None

    def set_session_key(self, session_key: str) -> None:
        self._session_key = session_key

    @property
    def name(self) -> str:
        return "list_dir"
    
    @property
    def description(self) -> str:
        return "List the contents of a directory."
    
    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "The directory path to list"
                }
            },
            "required": ["path"]
        }
    
    async def execute(self, path: str, **kwargs: Any) -> str:
        try:
            if self._sandbox_manager and self._session_key:
                sandbox = await self._sandbox_manager.get_sandbox(self._session_key)
                input_path = Path(path)

                if input_path.is_absolute():
                    if path == "/":
                        sandbox_path = sandbox.workspace
                    else:
                        resolved = input_path.resolve()
                        sandbox_resolved = sandbox.workspace.resolve()
                        if not str(resolved).startswith(str(sandbox_resolved)):
                            return f"Error: Absolute path outside sandbox: {path}"
                        sandbox_path = resolved
                else:
                    sandbox_path = sandbox.workspace / path

                if not sandbox_path.exists():
                    return f"Error: Directory not found: {path}"
                if not sandbox_path.is_dir():
                    return f"Error: Not a directory: {path}"

                items = []
                for item in sorted(sandbox_path.iterdir()):
                    prefix = "📁 " if item.is_dir() else "📄 "
                    items.append(f"{prefix}{item.name}")

                if not items:
                    return f"Directory {path} is empty"

                return "\n".join(items)

            dir_path = _resolve_path(path, self._allowed_dir)
            if not dir_path.exists():
                return f"Error: Directory not found: {path}"
            if not dir_path.is_dir():
                return f"Error: Not a directory: {path}"

            items = []
            for item in sorted(dir_path.iterdir()):
                prefix = "📁 " if item.is_dir() else "📄 "
                items.append(f"{prefix}{item.name}")

            if not items:
                return f"Directory {path} is empty"

            return "\n".join(items)
        except PermissionError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
