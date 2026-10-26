"""File system tools: read, write, edit."""

from typing import TYPE_CHECKING, Any

from vikingbot.agent.tools.base import Tool

if TYPE_CHECKING:
    from vikingbot.agent.tools.base import ToolContext


class ReadFileTool(Tool):
    """Tool to read file contents."""

    @property
    def name(self) -> str:
        return "read_file"

    @property
    def description(self) -> str:
        return "Read a text file, optionally selecting a range of lines with offset and limit."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "The file path to read"},
                "offset": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "Starting line number (1-based, defaults to 1).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "Maximum lines to return; omitted reads to the end of the file.",
                },
            },
            "required": ["path"],
        }

    @property
    def resource_inputs(self) -> dict[str, str]:
        return {"path": "local_file"}

    async def execute(
        self,
        tool_context: "ToolContext",
        path: str,
        offset: int = 1,
        limit: int | None = None,
        **kwargs: Any,
    ) -> str:
        """Return the selected lines with original line endings, or an error string.

        Offset is 1-based; a zero limit or an offset past EOF returns an empty string.
        Omitting both bounds preserves the complete file content on every sandbox backend.
        """
        if offset < 1 or (limit is not None and limit < 0):
            return "Error: offset must be >= 1 and limit must be >= 0"
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            content = await sandbox.read_file(path)
            if offset == 1 and limit is None:
                return content
            start = offset - 1
            return "".join(
                content.splitlines(keepends=True)[start : None if limit is None else start + limit]
            )
        except FileNotFoundError as e:
            return f"Error: {e}"
        except IOError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error reading file: {str(e)}"


class WriteFileTool(Tool):
    """Tool to write content to a file."""

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
                "path": {"type": "string", "description": "The file path to write to"},
                "content": {"type": "string", "description": "The content to write"},
            },
            "required": ["path", "content"],
        }

    async def execute(
        self, tool_context: "ToolContext", path: str, content: str, **kwargs: Any
    ) -> str:
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            await sandbox.write_file(path, content)
            return f"Successfully wrote {len(content)} bytes to {path}"
        except IOError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error writing file: {str(e)}"


class EditFileTool(Tool):
    """Tool to edit a file by replacing text."""

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
                "path": {"type": "string", "description": "The file path to edit"},
                "old_text": {"type": "string", "description": "The exact text to find and replace"},
                "new_text": {"type": "string", "description": "The text to replace with"},
            },
            "required": ["path", "old_text", "new_text"],
        }

    @property
    def resource_inputs(self) -> dict[str, str]:
        return {"path": "local_file"}

    async def execute(
        self, tool_context: "ToolContext", path: str, old_text: str, new_text: str, **kwargs: Any
    ) -> str:
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            content = await sandbox.read_file(path)

            if old_text not in content:
                return "Error: old_text not found in file. Make sure it matches exactly."

            count = content.count(old_text)
            if count > 1:
                return f"Warning: old_text appears {count} times. Please provide more context to make it unique."

            new_content = content.replace(old_text, new_text, 1)
            await sandbox.write_file(path, new_content)

            return f"Successfully edited {path}"
        except FileNotFoundError as e:
            return f"Error: {e}"
        except IOError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error editing file: {str(e)}"


class ListDirTool(Tool):
    """Tool to list directory contents."""

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
            "properties": {"path": {"type": "string", "description": "The directory path to list"}},
            "required": ["path"],
        }

    @property
    def resource_inputs(self) -> dict[str, str]:
        return {"path": "local_directory"}

    async def execute(self, tool_context: "ToolContext", path: str, **kwargs: Any) -> str:
        try:
            sandbox = await tool_context.sandbox_manager.get_sandbox(tool_context.session_key)
            items = await sandbox.list_dir(path)

            if not items:
                return f"Directory {path} is empty"

            formatted_items = []
            for name, is_dir in items:
                prefix = "📁 " if is_dir else "📄 "
                formatted_items.append(f"{prefix}{name}")

            return "\n".join(formatted_items)
        except FileNotFoundError as e:
            return f"Error: {e}"
        except IOError as e:
            return f"Error: {e}"
        except Exception as e:
            return f"Error listing directory: {str(e)}"
