import json
import re
from datetime import datetime
from typing import Any, Dict, Optional

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.utils.time_utils import parse_iso_datetime

# Regex patterns for MEMORY_FIELDS HTML comment
_MEMORY_FIELDS_PATTERN = re.compile(r"\n\n<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->", re.DOTALL)
_MEMORY_FIELDS_PATTERN_END = re.compile(r"<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->$", re.DOTALL)

DEFAULT_TRUNCATE_MAX_CHARS = 1000


def _serialize_datetime(obj: Any) -> Any:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def _deserialize_datetime(metadata: Dict[str, Any]) -> Dict[str, Any]:
    result = metadata.copy()
    for key in ["created_at", "updated_at"]:
        if key in result and isinstance(result[key], str):
            try:
                result[key] = parse_iso_datetime(result[key])
            except (ValueError, TypeError):
                pass
    return result


def _serialize_with_metadata(
    metadata: Dict[str, Any],
    content_template: str = None,
    extract_context: Any = None,
) -> str:
    content = metadata.pop("content", "") or ""

    if content_template:
        try:
            import jinja2
            from jinja2 import Environment

            env = Environment(autoescape=False, undefined=jinja2.DebugUndefined)
            template_vars = metadata.copy()
            template_vars["extract_context"] = extract_context

            jinja_template = env.from_string(content_template)
            content = jinja_template.render(**template_vars).strip()
        except Exception:
            pass

    clean_metadata = {k: v for k, v in metadata.items() if v is not None}

    if not clean_metadata:
        return content

    metadata_json = json.dumps(
        clean_metadata, indent=2, default=_serialize_datetime, ensure_ascii=False
    )

    comment = f"\n\n<!-- MEMORY_FIELDS\n{metadata_json}\n-->"

    if not content or not content.strip():
        return comment.lstrip()

    return content + comment


class MemoryFileUtils:
    """Unified read/write API for memory files.

    Encapsulates parsing + strip_links (read) and serialize + render_links (write).
    All other utilities (deserialize_content, serialize_with_metadata, etc.) are
    internal implementation details not exposed to callers.
    """

    @staticmethod
    def read(raw_content: str, uri: Optional[str] = None) -> MemoryFile:
        """Parse a memory file and return a MemoryFile with markdown links preserved."""
        parsed = parse_memory_file_with_fields(raw_content)
        parsed = _deserialize_datetime(parsed)
        return MemoryFile.from_parsed(uri=uri, parsed=parsed)

    @staticmethod
    def write(
        memory_file: MemoryFile,
        content_template: Optional[str] = None,
        extract_context: Any = None,
    ) -> str:
        """Serialize a MemoryFile as plain-text body plus MEMORY_FIELDS metadata."""
        metadata = memory_file.to_metadata()
        return _serialize_with_metadata(
            metadata,
            content_template=content_template,
            extract_context=extract_context,
        )

    @staticmethod
    def truncate_content(content: str, max_chars: int = DEFAULT_TRUNCATE_MAX_CHARS) -> str:
        """Truncate content to max_chars while keeping complete lines."""
        if len(content) <= max_chars:
            return content
        truncated = content[:max_chars]
        last_newline = truncated.rfind("\n")
        if last_newline > 0:
            truncated = truncated[:last_newline]
        return truncated + f"\n... [truncated {len(content) - len(truncated)} chars]"
