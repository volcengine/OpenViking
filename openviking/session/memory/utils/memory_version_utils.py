from datetime import datetime, timezone
from typing import Any, Optional

from diff_match_patch import diff_match_patch

from openviking.session.memory.dataclass import MemoryFile, VersionHistory, VersionHistoryItem
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.utils.time_utils import parse_iso_datetime


def make_reverse_diff(current_text: str, previous_text: str) -> str:
    """Create reverse diff payload from current text to previous text."""
    dmp = diff_match_patch()
    patches = dmp.patch_make(current_text, previous_text)
    return dmp.patch_toText(patches)


def apply_reverse_diff(current_text: str, reverse_diff: Optional[str]) -> str:
    """Apply reverse diff to current text and return previous text."""
    if not reverse_diff:
        return current_text

    dmp = diff_match_patch()
    patches = dmp.patch_fromText(reverse_diff)
    previous_text, _ = dmp.patch_apply(patches, current_text)
    return previous_text


def trim_versions(
    version_history: VersionHistory | dict[str, Any] | None, limit: int = 100
) -> VersionHistory | None:
    """Trim version items to the latest ``limit`` items."""
    if version_history is None:
        return None
    vh = _to_version_history(version_history)
    vh.versions = list(vh.versions[:limit])
    return vh


def is_version_visible(version_history: VersionHistory | dict[str, Any] | None) -> bool:
    """Return whether current version is visible."""
    vh = _to_version_history(version_history)
    if vh is None:
        return True
    return (vh.status or "active") != "deleted"


def resolve_version_for_data_version(
    memory_file: MemoryFile, data_version: int
) -> Optional[dict[str, Any]]:
    """Resolve the latest version state visible at ``<= data_version``.

    Returns metadata describing the matched state or ``None`` when no usable
    historical state exists.
    """
    current_version = _resolve_current_version(memory_file)
    if current_version is None:
        return None
    if current_version <= data_version:
        return {
            "data_version": current_version,
            "status": _current_status(memory_file),
            "is_head": True,
            "revert_steps": 0,
        }

    vh = memory_file.version_history
    if not vh or not vh.versions:
        return None

    working_status = _current_status(memory_file)
    revert_steps = 0
    for i, item in enumerate(vh.versions):
        item_version = _item_version(item)
        if item_version is None:
            continue
        if item_version <= data_version:
            return {
                "data_version": item_version,
                "status": working_status,
                "is_head": revert_steps == 0,
                "revert_steps": revert_steps,
            }
        previous_item = vh.versions[i + 1] if i + 1 < len(vh.versions) else None
        if item.op == "create":
            return None
        revert_steps += 1
        working_status = _status_for_item(previous_item)
    return None


def materialize_memory_at_version(raw_content: str, data_version: int | None) -> Optional[str]:
    """Return raw memory file content materialized at the requested version.

    Returns ``None`` when the file has no visible/usable version at the target
    historical point.
    """
    memory_file = MemoryFileUtils.read(raw_content)

    if data_version is None:
        return raw_content if is_version_visible(memory_file.version_history) else None

    current_version = _resolve_current_version(memory_file)
    if current_version is None:
        # Unknown historical file: only current head is readable without version.
        return None

    if current_version <= data_version:
        return raw_content if _current_status(memory_file) != "deleted" else None

    vh = memory_file.version_history
    if not vh or not vh.versions:
        return None

    working_text = raw_content
    working_status = _current_status(memory_file)
    for i, item in enumerate(vh.versions):
        item_version = _item_version(item)
        if item_version is None:
            continue
        if item_version <= data_version:
            return working_text if working_status != "deleted" else None
        if item.op == "create":
            return None
        working_text = _materialize_previous_raw_text(working_text, item)
        previous_item = vh.versions[i + 1] if i + 1 < len(vh.versions) else None
        working_status = _status_for_item(previous_item)

    return None


def _materialize_previous_raw_text(current_raw_text: str, item: VersionHistoryItem) -> str:
    current_memory_file = MemoryFileUtils.read(current_raw_text)
    previous_business_text = apply_reverse_diff(current_memory_file.content, item.reverse_diff)
    current_memory_file.content = previous_business_text
    return MemoryFileUtils.write(current_memory_file)


def _resolve_current_version(memory_file: MemoryFile) -> Optional[int]:
    vh = memory_file.version_history
    if vh:
        if vh.data_version is not None:
            return int(vh.data_version)
        if vh.updated_at is not None:
            return _datetime_to_millis(vh.updated_at)

    # Compatibility fallback for older files where version-like fields may live
    # in MEMORY_FIELDS.
    extra = memory_file.extra_fields or {}
    if extra.get("data_version") is not None:
        try:
            return int(extra["data_version"])
        except Exception:
            pass
    if extra.get("updated_at") is not None:
        return _coerce_to_millis(extra["updated_at"])
    return None


def _current_status(memory_file: MemoryFile) -> str:
    if memory_file.version_history and memory_file.version_history.status:
        return memory_file.version_history.status
    return "active"


def _status_for_item(item: Optional[VersionHistoryItem]) -> str:
    if item is None:
        return "active"
    return "deleted" if item.op == "delete" else "active"


def _item_version(item: VersionHistoryItem) -> Optional[int]:
    if item.data_version is None:
        return None
    return int(item.data_version)


def _to_version_history(
    version_history: VersionHistory | dict[str, Any] | None,
) -> Optional[VersionHistory]:
    if version_history is None:
        return None
    if isinstance(version_history, VersionHistory):
        return version_history
    return VersionHistory.model_validate(version_history)


def _coerce_to_millis(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, datetime):
        return _datetime_to_millis(value)
    if isinstance(value, str):
        try:
            return int(value)
        except Exception:
            try:
                return _datetime_to_millis(parse_iso_datetime(value))
            except Exception:
                return None
    return None


def _datetime_to_millis(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)
