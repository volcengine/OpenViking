# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Template utilities for session memory rendering."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import jinja2
from jinja2 import meta


class TemplateUtils:
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
