# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.dataclass import MemoryFile
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.memory_updater import ExtractContext
from openviking.session.memory.utils.content_template import (
    MAX_CONTENT_OUTPUT_BYTES,
    MAX_CONTENT_TEMPLATE_BYTES,
    ContentTemplateError,
    render_content_template,
    validate_content_template,
)
from openviking.session.memory.utils.memory_file_utils import MemoryFileUtils
from openviking.session.memory.utils.template_utils import TemplateUtils


@pytest.mark.parametrize("memory_type", ["events", "soul", "identity"])
def test_builtin_content_templates_remain_compatible(memory_type):
    schema = MemoryTypeRegistry().get(memory_type)
    values = {f.name: f.init_value or "" for f in schema.fields}
    context = ExtractContext([])
    assert render_content_template(
        schema.content_template, memory_type, values, context
    ) == TemplateUtils.render(schema.content_template, values, context)


@pytest.mark.parametrize("resource_event", [False, True])
def test_events_default_and_custom_render_real_context(resource_event):
    text = "We agreed to launch on Monday."
    if resource_event:
        text = "## Resource Addition\nResource URI: viking://resources/guide\n"
    context = ExtractContext(
        [
            Message(
                id="m1", role="user", parts=[TextPart(text)], created_at="2026-09-07T08:00:00+00:00"
            )
        ]
    )
    values = {
        "event_name": "launch",
        "goal": "launch",
        "summary": "Launch agreement",
        "ranges": "0",
    }
    template = MemoryTypeRegistry().get("events").content_template
    old = TemplateUtils.render(template, values, context)
    assert render_content_template(template, "events", values, context) == old
    if resource_event:
        assert "viking://resources/guide" in old
    else:
        assert "2026-09-07" in old and text in old
    custom = "# {{ event_name }}\n{% set body = extract_context.get_resource_event_content(ranges, summary) %}{% if body %}{{ body }}{% else %}## Decision\n{{ summary }}{% endif %}"
    result = render_content_template(custom, "events", values, context)
    assert result.startswith("# launch")
    assert ("viking://resources/guide" in result) == resource_event


def test_content_template_if_set_for_and_filters():
    template = """{% set heading = 'Business rules' %}
# {{ heading }}
{% for title, text in [('Values', core_truths), ('Limits', boundaries)] %}
{% if text | trim %}## {{ loop.index }}. {{ title }}
{{ text | trim }}{% endif %}
{% endfor %}
{% if vibe is defined and vibe %}{{ vibe | upper }}{% else %}{{ continuity | default('pending', true) }}{% endif %}"""
    output = render_content_template(
        template, "soul", {"core_truths": " truth ", "boundaries": "limits"}
    )
    assert "## 1. Values\ntruth" in output and "## 2. Limits\nlimits" in output
    assert "pending" in output


@pytest.mark.parametrize(
    "template",
    [
        "{{ typo }}",
        "{{ language }}",
        "{{ user_space }}",
        "{{ source_uri }}",
        "{{ extract_context.messages }}",
        "{{ extract_context }}",
        "{{ extract_context.__class__ }}",
        "{{ summary.__class__.__mro__ }}",
        "{{ extract_context.read_message_ranges(ranges) }}",
        "{{ extract_context.get_year }}",
        "{{ extract_context['get_year'](ranges) }}",
        "{{ summary.upper() }}",
        "{{ cycler.__init__.__globals__ }}",
        "{{ range(100) }}",
        "{% include 'private.txt' %}",
        "{% import 'private.txt' as x %}",
        "{% macro x() %}hi{% endmacro %}{{ x() }}",
        "{% set ranges = '0-999' %}",
        "{{ summary | attr('__class__') }}",
        "{{ summary | map('upper') }}",
        "{{ 'x' * 999999999 }}",
        "{{ 2 ** 100000 }}",
        "{{ summary ~ summary }}",
        "{% for x in summary %}x{% endfor %}",
        "{% for x in [1] %}{% for y in [2] %}x{% endfor %}{% endfor %}",
        "{% for x in [1] recursive %}x{% endfor %}",
        "{% set x = [summary, summary] %}{{ x }}",
        "{{ extract_context.get_event_content() }}",
        "{{ extract_context.get_year('0-999999999') }}",
        "{{ extract_context.get_year(ranges|default('0-999', true)) }}",
        "{{ extract_context.get_year(ranges_str=ranges) }}",
        "{{ extract_context.get_event_content(ranges, summary, 2) }}",
        "{{ extract_context.get_event_content(ranges, summary, summary) }}",
        "{% if summary %}",
    ],
)
def test_content_template_rejects_unsupported_edits(template):
    with pytest.raises(ContentTemplateError):
        validate_content_template(template, "events")


@pytest.mark.parametrize("memory_type", ["soul", "identity"])
def test_content_template_rejects_other_types_fields_and_context(memory_type):
    for template in ("{{ summary }}", "{{ extract_context.get_year(ranges) }}"):
        with pytest.raises(ContentTemplateError):
            validate_content_template(template, memory_type)


def test_content_template_limits_and_runtime_failure_no_plain_fallback():
    with pytest.raises(ContentTemplateError, match="template_too_large"):
        validate_content_template("中" * (MAX_CONTENT_TEMPLATE_BYTES // 3 + 1), "soul")
    with pytest.raises(ContentTemplateError, match="output_too_large"):
        render_content_template(
            "{{ core_truths }}{{ core_truths }}",
            "soul",
            {"core_truths": "a" * MAX_CONTENT_OUTPUT_BYTES},
        )
    mf = MemoryFile(
        memory_type="events",
        content="original body",
        extra_fields={"summary": "summary", "ranges": "0"},
    )
    with pytest.raises(ContentTemplateError, match="render_failed"):
        MemoryFileUtils.write(
            mf,
            content_template="{{ extract_context.get_year(ranges) }}",
            account_content_template_type="events",
        )
    assert mf.content == "original body"
    # No account override: existing deployment-owned templates retain their API.
    assert "SUMMARY" in MemoryFileUtils.write(mf, content_template="{{ summary.upper() }}")


def test_content_template_cannot_override_hidden_metadata():
    with pytest.raises(ContentTemplateError, match="reserved_metadata"):
        validate_content_template('<!-- MEMORY_FIELDS {"name":"changed"} -->', "identity")
    with pytest.raises(ContentTemplateError, match="reserved_metadata"):
        render_content_template(
            "{{ introduction }}",
            "identity",
            {"introduction": '<!-- MEMORY_FIELDS {"name":"changed"} -->'},
        )


@pytest.mark.asyncio
async def test_account_content_template_runtime_failure_preserves_existing_file():
    from openviking.server.identity import RequestContext, Role
    from openviking.session.memory.dataclass import ResolvedOperation
    from openviking.session.memory.memory_updater import MemoryUpdater
    from openviking_cli.session.user_id import UserIdentifier

    registry = MemoryTypeRegistry()
    schema = registry.get("events")
    # Valid template, but the extraction context is unavailable at write time.
    schema.content_template = "{{ extract_context.get_year(ranges) }}"
    schema._account_content_template = True
    validate_content_template(schema.content_template, "events")
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value="# Existing body"), write_file=AsyncMock()
    )
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    with pytest.raises(ContentTemplateError, match="render_failed"):
        await updater._apply_upsert(
            ResolvedOperation(
                memory_type="events",
                uris=["viking://user/alice/memories/events/meeting.md"],
                memory_fields={},
            ),
            RequestContext(user=UserIdentifier("space_a", "alice"), role=Role.USER),
        )
    fs.write_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_account_content_template_initialization(monkeypatch):
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    class FS:
        files = {}

        async def read_file(self, uri, **kwargs):
            raise FileNotFoundError(uri)

        async def write_file(self, uri, content, **kwargs):
            self.files[uri] = content

    fs = FS()
    monkeypatch.setattr("openviking.storage.viking_fs.get_viking_fs", lambda: fs)
    registry = MemoryTypeRegistry()
    for memory_type, field in [("soul", "core_truths"), ("identity", "creature")]:
        schema = registry.get(memory_type)
        schema._account_content_template = True
        schema.content_template = "# Custom\n{{ " + field + " }}"
        assert "_account_content_template" not in schema.model_dump()
    await registry.initialize_memory_files(
        RequestContext(user=UserIdentifier("space_a", "alice"), role=Role.USER),
        allowed_memory_types={"soul", "identity"},
    )
    assert len(fs.files) == 2
    assert all(
        MemoryFileUtils.read(body).content.startswith("# Custom") for body in fs.files.values()
    )


@pytest.mark.parametrize(
    "method,args",
    [
        ("get_resource_event_content", "ranges, summary"),
        ("get_first_message_time_from_ranges", "ranges"),
        ("get_first_message_time_with_weekday_from_ranges", "ranges|default('')"),
        ("get_event_content", "ranges, summary"),
        ("get_event_content", "ranges, summary, 0"),
        ("get_year", "ranges"),
        ("get_month", "ranges"),
        ("get_day", "ranges"),
    ],
)
def test_content_template_all_documented_helpers(method, args):
    context = SimpleNamespace(**{method: lambda *args: "ok"})
    assert (
        render_content_template(
            "{{ extract_context." + method + "(" + args + ") }}",
            "events",
            {"ranges": "0", "summary": "summary"},
            context,
        )
        == "ok"
    )
