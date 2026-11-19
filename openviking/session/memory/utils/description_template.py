# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""One restricted Jinja contract for deployment and account descriptions."""

from typing import Any, Mapping

from jinja2 import TemplateError, Undefined

from openviking.session.memory.utils.content_template import (
    ContentTemplateError,
    _environment,
    _parse,
)

MAX_DESCRIPTION_CHARS = 50_000
MAX_DESCRIPTION_OUTPUT_BYTES = 1024 * 1024
DESCRIPTION_VARIABLES = frozenset({"language"})


class DescriptionTemplateError(ValueError):
    def __init__(
        self,
        reason: str,
        line: int = 0,
        field: str = "description",
        *,
        memory_type: str | None = None,
        variable: str | None = None,
    ):
        self.reason = reason
        self.line = line
        self.field = field
        self.memory_type = memory_type
        self.variable = variable
        subject = f"memory type {memory_type!r} " if memory_type else ""
        detail = f" '{variable}'" if variable else ""
        super().__init__(
            f"{subject}{field}: {reason}{detail}" + (f" (line {line})" if line else "")
        )


def _parse_description(template: str, env, *, field: str = "description"):
    if not isinstance(template, str):
        raise DescriptionTemplateError("invalid_template", field=field)
    if len(template) > MAX_DESCRIPTION_CHARS:
        raise DescriptionTemplateError("template_too_large", field=field)
    # Empty deployment descriptions are valid; the editing API separately
    # requires nonempty supplied descriptions. Plain text needs no special mode.
    if not template.strip():
        return env.parse(template)
    try:
        return _parse(
            template,
            "description",
            env,
            variables=DESCRIPTION_VARIABLES,
            max_template_bytes=4 * MAX_DESCRIPTION_CHARS,
        )
    except ContentTemplateError as exc:
        raise DescriptionTemplateError(exc.reason, exc.line, field, variable=exc.variable) from exc


def validate_description_template(
    template: str,
    *,
    field: str = "description",
    memory_type: str | None = None,
) -> None:
    try:
        _parse_description(template, _environment(), field=field)
    except DescriptionTemplateError as exc:
        raise DescriptionTemplateError(
            exc.reason, exc.line, exc.field, memory_type=memory_type, variable=exc.variable
        ) from exc


def render_description_template(
    template: str,
    context: Mapping[str, Any],
    *,
    strip: bool = True,
    field: str = "description",
    memory_type: str | None = None,
) -> str:
    """Render one schema description.

    ``field``/``memory_type`` only label the error raised on an unsupported
    template; extraction has no reference back to the template file.
    """
    env = _environment()
    # Preserve plain schema descriptions byte-for-byte when strip=False, as
    # before; templated descriptions keep Jinja's existing newline behavior.
    env.keep_trailing_newline = not any(marker in template for marker in ("{{", "{%", "{#"))
    try:
        tree = _parse_description(template, env, field=field)
    except DescriptionTemplateError as exc:
        raise DescriptionTemplateError(
            exc.reason, exc.line, exc.field, memory_type=memory_type, variable=exc.variable
        ) from exc
    # Preserve the existing optional language context: missing variables retain
    # Jinja's undefined tests/empty output, without supplying a new language.
    env.undefined = Undefined
    values = {name: context[name] for name in DESCRIPTION_VARIABLES if name in context}
    if any(
        type(value) is not str or len(value.encode("utf-8")) > MAX_DESCRIPTION_OUTPUT_BYTES
        for value in values.values()
    ):
        raise DescriptionTemplateError(
            "invalid_context_value", field=field, memory_type=memory_type
        )
    try:
        chunks = []
        size = 0
        for chunk in env.from_string(tree).generate(**values):
            size += len(chunk.encode("utf-8"))
            if size > MAX_DESCRIPTION_OUTPUT_BYTES:
                raise DescriptionTemplateError("output_too_large")
            chunks.append(chunk)
        result = "".join(chunks)
        return result.strip() if strip else result
    except TemplateError as exc:
        raise DescriptionTemplateError("render_failed") from exc
