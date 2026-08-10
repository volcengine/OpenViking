import json
import logging
import re
from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.utils.link_renderer import LinkRenderer
from openviking.session.memory.utils.messages import parse_memory_file_with_fields
from openviking.session.memory.utils.uri import render_template
from openviking.utils.time_utils import parse_iso_datetime

logger = logging.getLogger(__name__)

# Regex patterns for MEMORY_FIELDS HTML comment
_MEMORY_FIELDS_PATTERN = re.compile(r"\n\n<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->", re.DOTALL)
_MEMORY_FIELDS_PATTERN_END = re.compile(r"<!--\s*MEMORY_FIELDS\s*\n(.*?)\n-->$", re.DOTALL)

DEFAULT_TRUNCATE_MAX_CHARS = 1000


def memory_version_from_fields(fields: Optional[Dict[str, Any]], *, default: int = 1) -> int:
    """Return a positive MEMORY_FIELDS version, falling back to ``default``."""
    try:
        version = int((fields or {}).get("version"))
    except (TypeError, ValueError):
        return default
    return version if version > 0 else default


def next_memory_version(old_file: Optional[MemoryFile]) -> int:
    """Return the next persisted MEMORY_FIELDS version for a write."""
    if old_file is None:
        return 1
    return memory_version_from_fields(old_file.extra_fields, default=1) + 1


def bump_memory_version(memory_file: MemoryFile) -> None:
    """Increment a MemoryFile's persisted MEMORY_FIELDS version in-place."""
    memory_file.extra_fields["version"] = memory_version_from_fields(
        memory_file.extra_fields, default=1
    ) + 1


_NON_SEMANTIC_MEMORY_FIELDS = {
    "version",
    "source_extraction_id",
    "source_extraction_ids",
    "last_update_trace_id",
    # These isolation/serialization fields do not describe the memory itself.
    "user_id",
    "user_ids",
    "_uri",
    "memory_type",
}


def memory_type_from_uri(uri: str) -> Optional[str]:
    """Return the memory type from a canonical OpenViking memory URI.

    Locate the memory root from the namespace grammar instead of searching for
    the first segment named ``memories``.  The latter is ambiguous when a user
    ID or a deeper category is itself named ``memories``.
    """
    raw_uri = str(uri or "")
    parsed = urlsplit(raw_uri)
    if parsed.scheme and parsed.netloc:
        parts = [parsed.netloc, *[part for part in parsed.path.split("/") if part]]
    else:
        parts = [part for part in raw_uri.split("/") if part]

    memory_root_index: Optional[int] = None
    if parts and parts[0] == "user":
        # Canonical peer memory: user/<uid>/peers/<peer>/memories/<type>/...
        if len(parts) > 5 and parts[2] == "peers" and parts[4] == "memories":
            memory_root_index = 4
        # Canonical user memory: user/<uid>/memories/<type>/...
        elif len(parts) > 3 and parts[2] == "memories":
            memory_root_index = 2
        # Legacy peer memory without an explicit user ID.
        elif len(parts) > 4 and parts[1] == "peers" and parts[3] == "memories":
            memory_root_index = 3
        # Legacy user memory: user/memories/<type>/...
        elif len(parts) > 2 and parts[1] == "memories":
            memory_root_index = 1
    elif (
        len(parts) > 3
        and parts[0] == "agent"
        and parts[2] == "memories"
    ):
        # Preserve the corresponding agent namespace accepted by VikingURI.
        memory_root_index = 2

    if memory_root_index is None or memory_root_index + 1 >= len(parts):
        return None
    memory_type = parts[memory_root_index + 1].removesuffix(".md")
    return memory_type or None


def _known_memory_types(memory_file: MemoryFile) -> set[str]:
    """Collect explicit and URI-derived schema ownership signals.

    Legacy memory files do not always serialize ``memory_type``.  Their URI is
    still authoritative, so a missing explicit value must not turn every
    otherwise-identical legacy update into a change.  Conversely, retaining
    all available signals catches both a real type change and an inconsistent
    file whose embedded type disagrees with its canonical path.
    """
    known = {
        str(value)
        for value in (
            memory_file.memory_type,
            (memory_file.extra_fields or {}).get("memory_type"),
        )
        if value
    }
    uri_memory_type = memory_type_from_uri(str(memory_file.uri or ""))
    if uri_memory_type:
        known.add(uri_memory_type)
    return known


def memory_files_semantically_equal(
    before: Optional[MemoryFile], after: Optional[MemoryFile]
) -> bool:
    """Return whether two files carry the same user-visible memory.

    Version and extraction provenance are write bookkeeping.  Comparing them
    as content makes an otherwise identical extraction look like a real edit,
    which in turn bumps the version, rewrites the vector, and regenerates the
    overview.  Stored content is compared verbatim: ``plain_content`` strips
    every relative Markdown target, including user-authored links that are not
    represented by ``links`` metadata, and could therefore hide a real edit.
    """
    if before is None or after is None:
        return False

    def _semantic_fields(memory_file: MemoryFile) -> Dict[str, Any]:
        return {
            key: value
            for key, value in dict(memory_file.extra_fields or {}).items()
            if key not in _NON_SEMANTIC_MEMORY_FIELDS
        }

    before_memory_types = _known_memory_types(before)
    after_memory_types = _known_memory_types(after)
    same_memory_type = before_memory_types == after_memory_types

    return (
        same_memory_type
        and before.content == after.content
        and before.links == after.links
        and before.backlinks == after.backlinks
        and _semantic_fields(before) == _semantic_fields(after)
    )


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




def _uri_basename(uri: str) -> str:
    name = str(uri or "").rstrip("/").rsplit("/", 1)[-1]
    return name.removesuffix(".md")


def _template_link_target(source_uri: Optional[str], target_uri: str) -> str:
    if source_uri and target_uri:
        return LinkRenderer.relative_path(str(source_uri), str(target_uri)) or str(target_uri)
    return str(target_uri or "")

def _serialize_with_metadata(
    metadata: Dict[str, Any],
    content_template: str = None,
    extract_context: Any = None,
    source_uri: Optional[str] = None,
    render_links: bool = True,
) -> str:
    content = metadata.pop("content", "") or ""

    if content_template:
        try:
            template_vars = metadata.copy()
            template_vars["content"] = content
            template_vars.setdefault("links", [])
            template_vars.setdefault("backlinks", [])
            template_vars["source_uri"] = source_uri or ""
            template_vars["uri_basename"] = _uri_basename
            template_vars["link_target"] = lambda target_uri: _template_link_target(source_uri, target_uri)
            content = render_template(content_template, template_vars, extract_context)
        except Exception:
            logger.exception(
                "Failed to render memory content template; using plain content fallback"
            )

    clean_metadata = {k: v for k, v in metadata.items() if v is not None}

    if not clean_metadata:
        return content

    clean_metadata.pop("_uri", None)
    links = clean_metadata.get("links")
    if render_links and isinstance(links, list) and source_uri:
        content = LinkRenderer.render_links(content, str(source_uri), links)

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
        render_links: bool = True,
    ) -> str:
        """Serialize a MemoryFile as plain-text body plus MEMORY_FIELDS metadata."""
        metadata = memory_file.to_metadata()
        return _serialize_with_metadata(
            metadata,
            content_template=content_template,
            extract_context=extract_context,
            source_uri=memory_file.uri,
            render_links=render_links,
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
