"""Shared target resolution for user-supplied content destinations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from openviking.core.path_variables import resolve_path_variables
from openviking.core.uri_validation import validate_optional_content_target_uri
from openviking_cli.exceptions import InvalidArgumentError

if TYPE_CHECKING:
    from openviking.server.identity import RequestContext


@dataclass(frozen=True)
class ContentTargetSpec:
    """Canonical content destination fields for resource and skill writes."""

    to: str = ""
    parent: str = ""
    create_parent: bool = False

    @classmethod
    def from_fields(
        cls,
        *,
        ctx: RequestContext,
        kind: str,
        to: Optional[str] = None,
        parent: Optional[str] = None,
        create_parent: bool = False,
    ) -> "ContentTargetSpec":
        resolved_to = resolve_path_variables(to) if to else None
        resolved_parent = resolve_path_variables(parent) if parent else None
        if (resolved_to or "").strip() and (resolved_parent or "").strip():
            raise InvalidArgumentError("Cannot specify both 'to' and 'parent' at the same time.")
        return cls(
            to=validate_optional_content_target_uri(
                resolved_to,
                ctx,
                kind=kind,
                field_name="to",
            ),
            parent=validate_optional_content_target_uri(
                resolved_parent,
                ctx,
                kind=kind,
                field_name="parent",
            ),
            create_parent=create_parent,
        )
