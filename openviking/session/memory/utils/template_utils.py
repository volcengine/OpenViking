# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Template utilities for session memory rendering."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import jinja2
from jinja2 import meta, nodes

# Keep aligned with ExtractContext's message-reading helpers. Archive/scope
# helpers such as get_source_archive_suffix do not require messages.
_MESSAGE_CONTEXT_MEMBERS = frozenset(
    {
        "messages",
        "read_message_ranges",
        "get_year",
        "get_month",
        "get_day",
        "get_first_message_time_from_ranges",
        "get_first_message_time_with_weekday_from_ranges",
        "get_timestamp_from_ranges",
        "get_session_timestamp",
        "get_event_content",
        "get_resource_event_content",
    }
)


class TemplateUtils:
    @staticmethod
    def requires_messages(template: str) -> bool:
        """Inspect actual Jinja references, not literal text or comments."""
        tree = jinja2.Environment(autoescape=False).parse(template)
        classified = set()
        for node in tree.find_all((nodes.Getattr, nodes.Getitem)):
            if not isinstance(node.node, nodes.Name) or node.node.name != "extract_context":
                continue
            if isinstance(node, nodes.Getattr):
                member = node.attr
            elif isinstance(node.arg, nodes.Const):
                member = node.arg.value
            else:
                # ponytail: unknown dynamic lookup requires messages; do not
                # interpret arbitrary Jinja expressions to infer its target.
                return True
            if member in _MESSAGE_CONTEXT_MEMBERS:
                return True
            classified.add(id(node.node))
        # ponytail: aliases/filters can hide method calls. Treat unclassified uses
        # conservatively instead of building a Jinja data-flow interpreter.
        return any(
            node.name == "extract_context" and node.ctx == "load" and id(node) not in classified
            for node in tree.find_all(nodes.Name)
        )

    @staticmethod
    def render(
        template: str,
        fields: Mapping[str, Any],
        extract_context: Any = None,
        *,
        debug_undefined: bool = False,
        keep_trailing_newline: bool = False,
        strip: bool = True,
    ) -> str:
        env = jinja2.Environment(
            autoescape=False,
            undefined=jinja2.DebugUndefined if debug_undefined else jinja2.Undefined,
            keep_trailing_newline=keep_trailing_newline,
        )
        template_vars = dict(fields)
        template_vars["extract_context"] = extract_context
        rendered = env.from_string(template).render(**template_vars)
        return rendered.strip() if strip else rendered

    @staticmethod
    def find_missing_variables(
        template: str,
        fields: Mapping[str, Any],
        *,
        ignored_variables: Iterable[str] | None = None,
    ) -> set[str]:
        parsed_template = jinja2.Environment(autoescape=False).parse(template)
        ignored = set(ignored_variables or ())
        return (
            meta.find_undeclared_variables(parsed_template)
            - set(fields)
            - ignored
            - {"extract_context"}
        )

    @staticmethod
    def referenced_variables(template: str) -> set[str]:
        """Return the top-level variables a template actually references.

        Uses Jinja's own parser so filters, functions, and attribute/method
        names (e.g. ``lower`` in ``{{ name|lower }}`` or ``get_year`` in
        ``{{ extract_context.get_year(ranges) }}``) are never mistaken for
        variables the way a plain substring/regex scan would.
        """
        parsed_template = jinja2.Environment(autoescape=False).parse(template)
        return meta.find_undeclared_variables(parsed_template)
