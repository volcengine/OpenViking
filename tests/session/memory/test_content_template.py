# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from openviking.message import Message, TextPart
from openviking.session.memory.account_templates import (
    _complete_template,
    _validate_template,
    memory_template_data,
)
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
def test_inherited_builtin_content_templates_keep_deployment_renderer(memory_type):
    schema = MemoryTypeRegistry().get(memory_type)
    complete = _complete_template(
        memory_template_data(schema), {"description": "Account instructions"}, memory_type
    )
    inherited = _validate_template(
        complete, memory_type, deployment_defaults=memory_template_data(schema)
    )
    assert inherited._account_content_template is False
    values = {f.name: f.init_value or "" for f in schema.fields}
    context = ExtractContext([])
    rendered = MemoryFileUtils.write(
        MemoryFile(memory_type=memory_type, extra_fields=values),
        content_template=inherited.content_template,
        extract_context=context,
        account_content_template_type=(
            inherited.memory_type if inherited._account_content_template else None
        ),
    )
    assert MemoryFileUtils.read(rendered).content == TemplateUtils.render(
        schema.content_template, values, context
    )


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
    rendered = MemoryFileUtils.write(
        MemoryFile(memory_type="events", extra_fields=values),
        content_template=template,
        extract_context=context,
    )
    assert MemoryFileUtils.read(rendered).content == old
    if resource_event:
        assert "viking://resources/guide" in old
    else:
        assert "2026-09-07" in old and text in old
    custom = "# {{ event_name }}\n{% set body = extract_context.get_resource_event_content(ranges, summary) %}{% if body %}{{ body }}{% else %}## Decision\n{{ summary }}{% endif %}"
    result = render_content_template(custom, "events", values, context)
    assert result.startswith("# launch")
    assert ("viking://resources/guide" in result) == resource_event


@pytest.mark.parametrize("memory_type", ["events", "soul", "identity"])
@pytest.mark.parametrize("edit", ["exact", "rstrip", "newline", "crlf", "heading"])
@pytest.mark.parametrize("message_kind", ["ordinary", "resource", "empty"])
def test_builtin_body_edits_publish_and_render(memory_type, edit, message_kind):
    defaults = memory_template_data(MemoryTypeRegistry().get(memory_type))
    original = defaults["content_template"]
    template = {
        "exact": original,
        "rstrip": original.rstrip(),
        "newline": original + "\n",
        "crlf": original.replace("\n", "\r\n"),
        "heading": "# Account memory\n" + original,
    }[edit]
    data = _complete_template(defaults, {"content_template": template}, memory_type)
    schema = _validate_template(data, memory_type, deployment_defaults=defaults)
    assert schema._account_content_template == (template != original)
    # The built-in syntax must pass the sandbox itself, not just inheritance.
    validate_content_template(template, memory_type)
    text = (
        "## Resource Addition\nResource URI: viking://resources/guide\n"
        if message_kind == "resource"
        else "We agreed to launch on Monday."
    )
    context = ExtractContext(
        []
        if message_kind == "empty"
        else [
            Message(
                id="m1", role="user", parts=[TextPart(text)], created_at="2026-09-07T08:00:00+00:00"
            )
        ]
    )
    values = {field.name: "Business fact" for field in schema.fields}
    values["ranges"] = "" if message_kind == "empty" else "0"
    expected = TemplateUtils.render(template, values, context)
    assert render_content_template(template, memory_type, values, context) == expected
    rendered = MemoryFileUtils.write(
        MemoryFile(memory_type=memory_type, extra_fields=values),
        content_template=schema.content_template,
        extract_context=context,
        account_content_template_type=memory_type if schema._account_content_template else None,
    )
    assert MemoryFileUtils.read(rendered).content == expected


@pytest.mark.parametrize("expression", ["default", "default()", "default('N/A')"])
@pytest.mark.parametrize("value_kind", ["undefined", "none", "empty", "text"])
def test_default_filter_preserves_deployment_semantics(expression, value_kind):
    from jinja2 import Undefined

    value = {"undefined": Undefined(name="date"), "none": None, "empty": "", "text": "date"}[
        value_kind
    ]
    context = SimpleNamespace(get_first_message_time_from_ranges=lambda *args: value)
    template = (
        "Date: {{ extract_context.get_first_message_time_from_ranges(ranges) | "
        + expression
        + " }}"
    )
    assert render_content_template(template, "events", {"ranges": ""}, context) == (
        TemplateUtils.render(template, {"ranges": ""}, context)
    )


def test_default_filter_rejects_untrusted_values_without_coercion():
    class Impostor:
        def __str__(self):
            pytest.fail("default must not coerce an untrusted object")

        def __bool__(self):
            pytest.fail("default must not evaluate untrusted truthiness")

    class StringSubclass(str):
        def __str__(self):
            pytest.fail("default must not coerce a string subclass")

    for value in (Impostor(), StringSubclass("text"), {}, 1, False):
        context = SimpleNamespace(get_event_content=lambda *args, result=value: result)
        with pytest.raises(ContentTemplateError, match="render_failed"):
            render_content_template(
                "{{ extract_context.get_event_content(ranges, summary) | default('fallback') }}",
                "events",
                {"ranges": "0"},
                context,
            )


@pytest.mark.parametrize(
    "expression",
    [
        "ranges",
        "ranges|default",
        "ranges|default()",
        "ranges|default('')",
        "ranges|default('0-999')",
        "ranges|default('')|trim",
        "ranges.strip()",
        "selected",
        "selected|trim|upper|lower",
        "ranges if summary else ''",
        "summary",
    ],
)
@pytest.mark.parametrize("ranges", ["", "0", "0-3,7"])
def test_event_helper_accepts_equivalent_range_expressions(expression, ranges):
    get_year = Mock(spec=[], return_value="2026")
    template = "{% set selected = ranges %}{{ extract_context.get_year(" + expression + ") }}"
    validate_content_template(template, "events")
    assert (
        render_content_template(
            template,
            "events",
            {"ranges": ranges, "summary": ranges},
            SimpleNamespace(get_year=get_year),
        )
        == "2026"
    )
    get_year.assert_called_once_with(ranges)


@pytest.mark.parametrize(
    "expression,ranges",
    [
        ("'0-999999999'", "0"),
        ("summary|default('')", "0"),
        ("selected", "0"),
        ("ranges or '0-999'", ""),
        ("ranges if not summary else summary", "0"),
        ("ranges|trim", " 0 "),
        ("none", "0"),
        ("false", "0"),
        ("1", "0"),
    ],
)
def test_event_helper_rejects_changed_or_nonstring_ranges_before_call(expression, ranges):
    get_year = Mock(spec=[], return_value="2026")
    template = "{% set selected = summary %}{{ extract_context.get_year(" + expression + ") }}"
    # Publication checks syntax; only extraction has the actual field values.
    validate_content_template(template, "events")
    with pytest.raises(ContentTemplateError, match="invalid_ranges"):
        render_content_template(
            template,
            "events",
            {"ranges": ranges, "summary": "0-999"},
            SimpleNamespace(get_year=get_year),
        )
    get_year.assert_not_called()


def test_event_helper_checks_nested_call_results_without_object_coercion():
    class Impostor:
        def __eq__(self, other):
            pytest.fail("Range validation must not compare arbitrary objects")

        def __str__(self):
            pytest.fail("Range validation must not coerce arbitrary objects")

        def __bool__(self):
            pytest.fail("Range validation must not evaluate arbitrary truthiness")

    class StringSubclass(str):
        def __eq__(self, other):
            pytest.fail("Range validation must not compare string subclasses")

    template = "{{ extract_context.get_year(extract_context.get_event_content(ranges, summary)) }}"
    validate_content_template(template, "events")
    for value in (Impostor(), StringSubclass("0"), {}, [], 0, False, None):
        get_content = Mock(spec=[], return_value=value)
        get_year = Mock(spec=[], return_value="2026")
        with pytest.raises(ContentTemplateError, match="invalid_ranges"):
            render_content_template(
                template,
                "events",
                {"ranges": "0", "summary": "summary"},
                SimpleNamespace(get_event_content=get_content, get_year=get_year),
            )
        get_content.assert_called_once_with("0", "summary")
        get_year.assert_not_called()


@pytest.mark.parametrize("flag", ["unsafe_callable", "alters_data"])
def test_event_helper_preserves_sandbox_callable_flags(flag):
    helper = Mock(spec=[], return_value="2026")
    setattr(helper, flag, True)
    with pytest.raises(ContentTemplateError, match="render_failed"):
        render_content_template(
            "{{ extract_context.get_year(ranges) }}",
            "events",
            {"ranges": "0"},
            SimpleNamespace(get_year=helper),
        )
    helper.assert_not_called()


def test_event_helper_checks_each_call_against_current_render_ranges():
    helper = Mock(spec=[], return_value="2026")
    context = SimpleNamespace(get_year=helper)
    template = "{% for selected in [ranges, summary] %}{{ extract_context.get_year(selected) }}{% endfor %}"
    for ranges in ("0", "1"):
        helper.reset_mock()
        assert (
            render_content_template(
                template, "events", {"ranges": ranges, "summary": ranges}, context
            )
            == "20262026"
        )
        assert [call.args for call in helper.call_args_list] == [(ranges,), (ranges,)]
        helper.reset_mock()
        with pytest.raises(ContentTemplateError, match="invalid_ranges"):
            render_content_template(
                template, "events", {"ranges": ranges, "summary": "0-999"}, context
            )
        # Earlier valid reads do not exempt subsequent calls from validation.
        helper.assert_called_once_with(ranges)


def test_content_template_if_set_for_and_string_methods():
    template = """{% set heading = 'Business rules' %}
# {{ heading }}
{% for title, text in [('Values', core_truths), ('Limits', boundaries)] %}
{% if text.strip() %}## {{ loop.index }}. {{ title }}
{{ text.strip() }}{% endif %}
{% endfor %}
{% if vibe is defined and vibe %}{{ vibe.upper() }}{% else %}{{ continuity or 'pending' }}{% endif %}"""
    output = render_content_template(
        template, "soul", {"core_truths": " truth ", "boundaries": "limits"}
    )
    assert "## 1. Values\ntruth" in output and "## 2. Limits\nlimits" in output
    assert "pending" in output


@pytest.mark.parametrize(
    "template, expected",
    [
        ("{{ summary.upper() }}", "ABC"),
        ("{{ summary.lower() }}", "abc"),
        ("{{ summary.strip() }}", "AbC"),
        ("{{ summary.strip().upper() }}", "ABC"),
        ("{{ ' HeLLo '.strip().lower() }}", "hello"),
        ("{% set text = summary %}{{ text.upper() }}", "ABC"),
        ("{% if summary.strip().lower() == 'abc' %}match{% endif %}", "match"),
        (
            "{% for title, text in [('Title', summary)] %}{{ title.upper() }}: {{ text.strip() }}{% endfor %}",
            "TITLE: AbC",
        ),
        ("{{ (summary or 'pending').upper() }}", "ABC"),
        ("{{ summary | trim }}", "AbC"),
        ("{{ summary | upper }}", "ABC"),
        ("{{ summary | lower }}", "abc"),
        ("{{ summary | trim | upper }}", "ABC"),
        ("{{ summary | default('pending') | trim | upper }}", "ABC"),
        ("{{ summary.strip() | lower }}", "abc"),
        ("{{ (summary | trim).upper() }}", "ABC"),
        ("{{ ' HeLLo ' | trim | lower }}", "hello"),
        ("{% set text = summary | trim %}{{ text | upper }}", "ABC"),
        ("{% if summary | trim | lower == 'abc' %}match{% endif %}", "match"),
        (
            "{% for title, text in [('Title', summary)] %}{{ title | upper }}: {{ text | trim }}{% endfor %}",
            "TITLE: AbC",
        ),
    ],
)
def test_content_template_string_formatting_matches_deployment_syntax(template, expected):
    fields = {"summary": " AbC "}
    validate_content_template(template, "events")
    assert render_content_template(template, "events", fields) == expected
    assert TemplateUtils.render(template, fields) == expected


def test_content_template_string_methods_on_helper_results_and_empty_fields():
    context = SimpleNamespace(get_event_content=lambda *args: " details ")
    assert (
        render_content_template(
            "{{ extract_context.get_event_content(ranges, summary).strip().upper() }}",
            "events",
            {"ranges": "0"},
            context,
        )
        == "DETAILS"
    )
    assert render_content_template("{{ summary.strip() or 'pending' }}", "events", {}) == "pending"


def test_content_template_filters_on_helper_results_and_empty_fields():
    context = SimpleNamespace(get_event_content=lambda *args: " details ")
    assert (
        render_content_template(
            "{{ extract_context.get_event_content(ranges, summary) | trim | upper }}",
            "events",
            {"ranges": "0"},
            context,
        )
        == "DETAILS"
    )
    assert render_content_template("{{ (summary | trim) or 'pending' }}", "events", {}) == "pending"


@pytest.mark.parametrize("operation", [".upper()", " | upper", " | lower", " | trim"])
@pytest.mark.parametrize("receiver_kind", ["object", "mapping", "str_subclass", "none", "int"])
def test_content_template_rejects_untrusted_string_receivers(receiver_kind, operation):
    touched = []

    class Impostor:
        def __str__(self):
            touched.append("string conversion")
            return "unsafe"

        @property
        def upper(self):
            touched.append("property")
            return lambda: "unsafe"

    class StringSubclass(str):
        def __str__(self):
            touched.append("subclass conversion")
            return "unsafe"

        def upper(self):
            touched.append("override")
            return "unsafe"

    receivers = {
        "object": Impostor(),
        "mapping": {"upper": lambda: touched.append("mapping")},
        "str_subclass": StringSubclass("text"),
        "none": None,
        "int": 1,
    }
    context = SimpleNamespace(get_event_content=lambda *args: receivers[receiver_kind])
    with pytest.raises(ContentTemplateError, match="render_failed"):
        render_content_template(
            "{{ extract_context.get_event_content(ranges, summary)" + operation + " }}",
            "events",
            {"ranges": "0"},
            context,
        )
    assert not touched


@pytest.mark.parametrize(
    "expression",
    [
        "summary | length",
        "summary | d('pending')",
        "summary | replace('a', 'b')",
        "summary | safe",
        "summary | unknown",
    ],
)
def test_content_template_rejects_unapproved_filters(expression):
    template = "{{ " + expression + " }}"
    with pytest.raises(ContentTemplateError, match="unsupported_filter"):
        validate_content_template(template, "events")
    with pytest.raises(ContentTemplateError, match="unsupported_filter"):
        render_content_template(template, "events", {"summary": "text"})


@pytest.mark.parametrize(
    "expression",
    [
        "summary | trim('x')",
        "summary | trim(chars='x')",
        "summary | upper(1)",
        "summary | lower(*summary)",
        "summary | trim(**summary)",
        "summary | default('pending', true)",
        "summary | default(default_value='pending')",
        "summary | default(summary)",
        "summary | default(1)",
        "summary | default(none)",
        "summary | default(*summary)",
        "summary | default(**summary)",
    ],
)
def test_content_template_rejects_filter_arguments(expression):
    template = "{{ " + expression + " }}"
    with pytest.raises(ContentTemplateError, match="invalid_arguments"):
        validate_content_template(template, "events")
    with pytest.raises(ContentTemplateError, match="invalid_arguments"):
        render_content_template(template, "events", {"summary": "text"})


def test_events_builtin_template_keeps_original_date_fallback():
    template = MemoryTypeRegistry().get("events").content_template
    assert "ranges|default('')" in template
    context = ExtractContext([])
    output = TemplateUtils.render(template, {"summary": "Summary", "ranges": ""}, context)
    # Preserve existing deployment behavior: default() handles undefined, not None.
    assert "# None ChatLog:" in output


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
        "{{ summary.upper }}",
        "{% set method = summary.upper %}{{ method() }}",
        "{{ summary.upper('x') }}",
        "{{ summary.strip('x') }}",
        "{{ summary.strip(chars='x') }}",
        "{{ summary.upper(*summary) }}",
        "{{ summary.upper(**summary) }}",
        "{{ summary.replace('a', 'b') }}",
        "{{ summary.format() }}",
        "{{ summary.strip().__class__ }}",
        "{{ (summary | trim).__class__ }}",
        "{{ summary | trim | attr('__class__') }}",
        "{% filter trim %}text{% endfilter %}",
        "{{ summary.upper.__self__ }}",
        "{{ extract_context.upper() }}",
        "{% for x in [1] %}{{ loop.upper() }}{% endfor %}",
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
@pytest.mark.parametrize("changed_ranges", [False, True])
async def test_account_content_template_runtime_failure_preserves_existing_file(changed_ranges):
    from openviking.server.identity import RequestContext, Role
    from openviking.session.memory.dataclass import ResolvedOperation
    from openviking.session.memory.memory_updater import MemoryUpdater
    from openviking_cli.session.user_id import UserIdentifier

    registry = MemoryTypeRegistry()
    schema = registry.get("events")
    # Valid syntax can still fail on unavailable context or changed ranges.
    schema.content_template = (
        "{% set selected = summary %}{{ extract_context.get_year(selected) }}"
        if changed_ranges
        else "{{ extract_context.get_year(ranges) }}"
    )
    schema._account_content_template = True
    validate_content_template(schema.content_template, "events")
    fs = SimpleNamespace(
        read_file=AsyncMock(return_value="# Existing body"), write_file=AsyncMock()
    )
    updater = MemoryUpdater(registry=registry)
    updater._viking_fs = fs
    get_year = Mock(spec=[], return_value="2026")
    with pytest.raises(
        ContentTemplateError, match="invalid_ranges" if changed_ranges else "render_failed"
    ):
        await updater._apply_upsert(
            ResolvedOperation(
                memory_type="events",
                uris=["viking://user/alice/memories/events/meeting.md"],
                memory_fields={"ranges": "0", "summary": "0-999"},
            ),
            RequestContext(user=UserIdentifier("space_a", "alice"), role=Role.USER),
            extract_context=SimpleNamespace(get_year=get_year) if changed_ranges else None,
        )
    fs.write_file.assert_not_awaited()
    get_year.assert_not_called()


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
        ("get_first_message_time_with_weekday_from_ranges", "ranges"),
        ("get_event_content", "ranges, summary"),
        ("get_event_content", "ranges, summary, 0"),
        ("get_year", "ranges"),
        ("get_month", "ranges"),
        ("get_day", "ranges"),
    ],
)
@pytest.mark.parametrize("expression", ["ranges", "selected", "ranges|default('')|trim", "''"])
def test_content_template_all_documented_helpers(method, args, expression):
    helper = Mock(spec=[], return_value="ok")
    context = SimpleNamespace(**{method: helper})
    prelude = "{% set selected = ranges %}"
    actual_args = args.replace("ranges", expression, 1)
    assert (
        render_content_template(
            prelude + "{{ extract_context." + method + "(" + actual_args + ") }}",
            "events",
            {"ranges": "0", "summary": "summary"},
            context,
        )
        == "ok"
    )
    expected_args = ("" if expression == "''" else "0",)
    if "summary" in args:
        expected_args += ("summary",)
    if args.endswith(", 0"):
        expected_args += (0,)
    helper.assert_called_once_with(*expected_args)
    helper.reset_mock()
    changed_args = args.replace("ranges", "'0-999'", 1)
    with pytest.raises(ContentTemplateError, match="invalid_ranges"):
        render_content_template(
            "{{ extract_context." + method + "(" + changed_args + ") }}",
            "events",
            {"ranges": "0", "summary": "summary"},
            context,
        )
    helper.assert_not_called()
