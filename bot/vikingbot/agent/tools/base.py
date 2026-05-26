"""Base class for agent tools."""

from dataclasses import dataclass

from vikingbot.config.schema import SessionKey
from vikingbot.sandbox.manager import SandboxManager


@dataclass
class ToolContext:
    """Context passed to tools during execution, containing runtime information.

    This class encapsulates all the runtime context that a tool might need during
    execution, including session identification, sandbox access, and sender information.

    Attributes:
        session_key: Unique identifier for the current session, typically in the format
            'channel:chat_id'.
        sandbox_manager: Optional manager for sandbox operations like file access and
            command execution. If provided, tools can perform sandboxed operations.
        workspace_id: Computed workspace identifier derived from the sandbox_manager
            and session_key. This determines the sandbox directory for the session.
        sender_id: Optional identifier for the message sender, used for tracking
            and permission checks.
        memory_user_ids: Optional list of user IDs for memory retrieval. Used by
            openviking_search tool to search specific users' memory spaces.

    Example:
        >>> context = ToolContext(
        ...     session_key=SessionKey(channel="telegram", chat_id="12345"),
        ...     sandbox_manager=sandbox_mgr,
        ...     sender_id="user_123"
        ... )
    """

    session_key: SessionKey = None
    sandbox_manager: SandboxManager | None = None
    workspace_id: str = sandbox_manager.to_workspace_id(session_key) if sandbox_manager else None
    sender_id: str | None = None
    memory_user_ids: list[str] | None = None


"""Base class for agent tools."""

from abc import ABC, abstractmethod
from typing import Any


class Tool(ABC):
    """
    Abstract base class for agent tools.

    Tools are capabilities that the agent can use to interact with the environment,
    such as reading files, executing commands, searching the web, etc. Each tool
    defines its own name, description, parameters schema, and execution logic.

    To create a new tool, subclass Tool and implement the required abstract
    properties and methods:
    - name: The unique identifier for the tool
    - description: Human-readable explanation of what the tool does
    - parameters: JSON Schema defining the tool's input parameters
    - execute(): The actual implementation of the tool's functionality

    Attributes:
        _TYPE_MAP: Internal mapping of JSON schema types to Python types for
            parameter validation.

    Example:
        >>> class GreetingTool(Tool):
        ...     @property
        ...     def name(self) -> str:
        ...         return "greet"
        ...
        ...     @property
        ...     def description(self) -> str:
        ...         return "Sends a greeting message"
        ...
        ...     @property
        ...     def parameters(self) -> dict[str, Any]:
        ...         return {
        ...             "type": "object",
        ...             "properties": {
        ...                 "name": {"type": "string", "description": "Name to greet"}
        ...             },
        ...             "required": ["name"]
        ...         }
        ...
        ...     async def execute(self, context: ToolContext, name: str) -> str:
        ...         return f"Hello, {name}!"
    """

    _TYPE_MAP = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """Tool name used in function calls."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Description of what the tool does."""
        pass

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema for tool parameters."""
        pass

    @abstractmethod
    async def execute(self, tool_context: ToolContext, **kwargs: Any) -> str:
        """
        Execute the tool with given parameters.

        Args:
            tool_context: Runtime context containing session key, sandbox manager, etc.
            **kwargs: Tool-specific parameters.

        Returns:
            String result of the tool execution.
        """
        pass

    def validate_params(self, params: dict[str, Any]) -> list[str]:
        """
        Validate tool parameters against the tool's JSON schema.

        This method validates that the provided parameters match the tool's
        defined schema, including type checking, required field validation,
        enum validation, and range constraints.

        Args:
            params: Dictionary of parameter names to values to validate.

        Returns:
            List of error messages. An empty list indicates the parameters
            are valid.

        Raises:
            ValueError: If the tool's parameter schema is not an object type.

        Example:
            >>> tool = MyTool()
            >>> errors = tool.validate_params({"name": "test", "count": 5})
            >>> if errors:
            ...     print("Validation failed:", errors)
            ... else:
            ...     print("Parameters are valid")
        """
        schema = self.parameters or {}
        if schema.get("type", "object") != "object":
            raise ValueError(f"Schema must be object type, got {schema.get('type')!r}")
        return self._validate(params, {**schema, "type": "object"}, "")

    def _validate(self, val: Any, schema: dict[str, Any], path: str) -> list[str]:
        """
        Recursively validate a value against a JSON schema.

        This internal method performs recursive validation of values against
        JSON schema definitions, supporting all common JSON schema features
        including type checking, enums, ranges, string length, object properties,
        and array items.

        Args:
            val: The value to validate.
            schema: The JSON schema to validate against.
            path: The current path in the data structure (for error messages).

        Returns:
            List of validation error messages. Empty list if validation passes.

        Note:
            This is an internal method used by validate_params(). It should
            not be called directly from outside the class.
        """
        t, label = schema.get("type"), path or "parameter"
        if t in self._TYPE_MAP and not isinstance(val, self._TYPE_MAP[t]):
            return [f"{label} should be {t}"]

        errors = []
        if "enum" in schema and val not in schema["enum"]:
            errors.append(f"{label} must be one of {schema['enum']}")
        if t in ("integer", "number"):
            if "minimum" in schema and val < schema["minimum"]:
                errors.append(f"{label} must be >= {schema['minimum']}")
            if "maximum" in schema and val > schema["maximum"]:
                errors.append(f"{label} must be <= {schema['maximum']}")
        if t == "string":
            if "minLength" in schema and len(val) < schema["minLength"]:
                errors.append(f"{label} must be at least {schema['minLength']} chars")
            if "maxLength" in schema and len(val) > schema["maxLength"]:
                errors.append(f"{label} must be at most {schema['maxLength']} chars")
        if t == "object":
            props = schema.get("properties", {})
            for k in schema.get("required", []):
                if k not in val:
                    errors.append(f"missing required {path + '.' + k if path else k}")
            for k, v in val.items():
                if k in props:
                    errors.extend(self._validate(v, props[k], path + "." + k if path else k))
        if t == "array" and "items" in schema:
            for i, item in enumerate(val):
                errors.extend(
                    self._validate(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]")
                )
        return errors

    def to_schema(self) -> dict[str, Any]:
        """
        Convert tool to OpenAI function schema format.

        This method transforms the tool's definition into the format expected by
        OpenAI's function calling API, which can be used with chat completions.

        Returns:
            Dictionary containing the function schema in OpenAI format with:
            - type: Always "function"
            - function: Object containing name, description, and parameters

        Example:
            >>> tool = MyTool()
            >>> schema = tool.to_schema()
            >>> print(schema)
            {
                'type': 'function',
                'function': {
                    'name': 'my_tool',
                    'description': 'Does something useful',
                    'parameters': {'type': 'object', 'properties': {...}}
                }
            }
        """
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
