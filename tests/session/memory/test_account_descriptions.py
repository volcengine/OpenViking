# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Deployment and account descriptions share one restricted Jinja contract."""

import pytest

from openviking.session.memory.account_templates import (
    EDITABLE_MEMORY_TEMPLATE_FIELDS,
    _complete_template,
    _parse_template,
    _validate_template,
    memory_template_data,
)
from openviking.session.memory.extraction_output_protocol import (
    ExtractionOutputContext,
    create_extraction_output_protocol,
)
from openviking.session.memory.memory_type_registry import MemoryTypeRegistry
from openviking.session.memory.page_id_map import PageIdMap
from openviking.session.memory.schema_model_generator import (
    SchemaModelGenerator,
    SchemaPromptGenerator,
)
from openviking.session.memory.utils.description_template import (
    MAX_DESCRIPTION_CHARS,
    MAX_DESCRIPTION_OUTPUT_BYTES,
    DescriptionTemplateError,
    render_description_template,
    validate_description_template,
)


@pytest.mark.parametrize(
    ("generator_type", "plain_expected", "template_expected"),
    [
        (SchemaModelGenerator, "  plain\n", "  EN  "),
        (SchemaPromptGenerator, "plain", "EN"),
    ],
)
def test_generators_render_description_strings(generator_type, plain_expected, template_expected):
    generator = generator_type([], template_context={"language": "en"})

    assert generator._render_description("  plain\n") == plain_expected
    assert generator._render_description("  {{ language.upper() }}  ") == template_expected
    assert generator._render_description("  {{ language | trim | upper }}  ") == template_expected
    assert generator._render_description("") == ""
    with pytest.raises(DescriptionTemplateError):
        generator._render_description("{{ language | length }}")


@pytest.mark.parametrize("memory_type", EDITABLE_MEMORY_TEMPLATE_FIELDS)
@pytest.mark.parametrize("origin", ["deployment", "account"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Plain instructions", "Plain instructions"),
        ("{{ language }}", "en"),
        (
            "{% if language == 'en' %}English{% elif language == 'ja' %}日本語{% else %}中文{% endif %}",
            "English",
        ),
        ("{{ language.strip().upper().lower() }}", "en"),
        ("{{ language | trim | upper | lower }}", "en"),
        ("{% set label = language | upper %}{{ label }}", "EN"),
        ("{{ language | default('en') | upper }}", "EN"),
        ("{% set label = language.upper() %}{{ label }}", "EN"),
        (
            "{% for title, value in [('Use', language), ('Also', 'dates')] %}{{ loop.index }} {{ title }} {{ value }};{% endfor %}",
            "1 Use en;2 Also dates;",
        ),
        (
            "{% if language is defined and language is string and language is not none %}{{ language }}{% endif %}",
            "en",
        ),
    ],
)
def test_descriptions_render_identically_in_all_schema_prompts(memory_type, origin, text, expected):
    defaults = memory_template_data(MemoryTypeRegistry().get(memory_type))
    editable = EDITABLE_MEMORY_TEMPLATE_FIELDS[memory_type]
    supplied = {
        "description": "TYPE: " + text,
        "fields": [{"name": name, "description": name + ": " + text} for name in editable],
    }
    data = _complete_template(defaults, supplied, memory_type)
    if origin == "deployment":
        schema = MemoryTypeRegistry(load_schemas=False)._parse_memory_type(data)
    else:
        schema = _validate_template(data, memory_type, deployment_defaults=defaults)
    schema = schema.model_copy(deep=True)
    assert not hasattr(schema, "_account_description")
    assert "_account_description" not in memory_template_data(schema)
    assert schema.description == supplied["description"]

    generator = SchemaModelGenerator([schema], template_context={"language": "en"})
    operations = generator.create_structured_operations_model()
    model = generator.create_flat_data_model(schema)
    prompts = SchemaPromptGenerator([schema], template_context={"language": "en"})
    type_prompt = prompts.generate_type_descriptions()
    field_prompt = prompts.generate_field_descriptions(memory_type)
    protocol_context = ExtractionOutputContext(
        operations_model=operations,
        schemas=(schema,),
        page_id_map=PageIdMap(),
        read_file_contents={},
        link_enabled=False,
        template_context={"language": "en"},
    )
    python_contract = create_extraction_output_protocol("python").render_contract(protocol_context)
    assert "TYPE: " + expected in operations.model_fields[memory_type].description
    assert "TYPE: " + expected in type_prompt
    assert "TYPE: " + expected in python_contract
    for field in schema.fields:
        if field.name in editable:
            assert not hasattr(field, "_account_description")
            assert "_account_description" not in field.model_dump()
            assert field.name + ": " + expected in model.model_fields[field.name].description
            assert field.name + ": " + expected in type_prompt
            assert field.name + ": " + expected in field_prompt
            assert field.name + ": " + expected in python_contract


@pytest.mark.parametrize("request_kind", ["empty", "roundtrip", "type_only", "field_only"])
def test_unchanged_deployment_descriptions_keep_existing_language_rendering(request_kind):
    deployment = MemoryTypeRegistry().get("profile")
    deployment.description = "TYPE {{ language.upper() }}"
    deployment.fields[0].description = "FIELD {{ language.upper() }}"
    defaults = memory_template_data(deployment)
    supplied = {
        "empty": {},
        "roundtrip": defaults,
        "type_only": {"description": "CUSTOM {{ language }}"},
        "field_only": {"fields": [{"name": "content", "description": "CUSTOM {{ language }}"}]},
    }[request_kind]
    data = _complete_template(defaults, supplied, "profile")
    schema = _validate_template(data, "profile", deployment_defaults=defaults)
    generator = SchemaModelGenerator([schema], template_context={"language": "en"})
    operations = generator.create_structured_operations_model()
    field_description = generator.create_flat_data_model(schema).model_fields["content"].description
    type_description = operations.model_fields["profile"].description
    assert ("CUSTOM en" if request_kind == "type_only" else "TYPE EN") in type_description
    assert ("CUSTOM en" if request_kind == "field_only" else "FIELD EN") in field_description
    assert not hasattr(deployment, "_account_description")
    assert not hasattr(deployment.fields[0], "_account_description")


@pytest.mark.parametrize("field", ["description", "fields.content.description"])
@pytest.mark.parametrize(
    "text",
    [
        "{{ cycler.__init__.__globals__.__builtins__.len('harmless') }}",
        "{% for n in range(3) %}x{% endfor %}",
        "{% include 'not_a_file' %}",
        "literal {{ and {% unclosed",
        "{{ language | length }}",
        "{{ language | trim('x') }}",
        "{{ language | attr('__class__') }}",
        "{{ summary }}",
        "{{ extract_context.get_year(ranges) }}",
        "{{ language[0] }}",
        "{{ language.__class__ }}",
        "{{ language.upper }}",
        "{{ language.strip('x') }}",
        "{{ language.replace('en', 'zh') }}",
        "{{ language * 1000000000 }}",
        "{% set language = 'override' %}{{ language }}",
        "{% set loop = 'override' %}{{ loop }}",
        "{% for n in language %}{{ n }}{% endfor %}",
        "{% for n in [1] %}{% for m in [2] %}x{% endfor %}{% endfor %}",
        "{% for n in [" + ",".join(["1"] * 33) + "] %}x{% endfor %}",
    ],
)
def test_unsupported_descriptions_rejected_on_publish_load_and_render(text, field):
    import yaml

    deployment = MemoryTypeRegistry().get("profile")
    defaults = memory_template_data(deployment)
    data = memory_template_data(deployment)
    supplied = (
        {"description": text}
        if field == "description"
        else {"fields": [{"name": "content", "description": text}]}
    )
    with pytest.raises(DescriptionTemplateError) as caught:
        _complete_template(defaults, supplied, "profile")
    assert caught.value.field == field
    # Old overrides remain readable/resettable but cannot bypass extraction
    # validation, even if they contain obsolete client trust flags.
    if field == "description":
        data["description"] = text
    else:
        data["fields"][0]["description"] = text
    data["_account_description"] = False
    data["fields"][0]["_account_description"] = False
    raw = yaml.safe_dump(data).encode()
    parsed = _parse_template(raw, "profile")
    with pytest.raises(DescriptionTemplateError):
        _validate_template(parsed, "profile", deployment_defaults=defaults)
    with pytest.raises(DescriptionTemplateError):
        render_description_template(text, {"language": "en"})
    # Deployment schemas cannot bypass the same policy by avoiding the Account loader.
    schema = MemoryTypeRegistry(load_schemas=False)._parse_memory_type(data)
    with pytest.raises(DescriptionTemplateError):
        SchemaModelGenerator([schema], {"language": "en"}).create_structured_operations_model()
    with pytest.raises(DescriptionTemplateError):
        SchemaPromptGenerator([schema], {"language": "en"}).generate_type_descriptions()


def test_deployment_change_does_not_change_old_description_rendering():
    deployment = MemoryTypeRegistry().get("profile")
    deployment.description = "TYPE {{ language.upper() }}"
    deployment.fields[0].description = "FIELD {{ language.upper() }}"
    stored = memory_template_data(deployment)
    old = _validate_template(stored, "profile", deployment_defaults=stored)
    deployment.description = "NEW TYPE {{ language }}"
    deployment.fields[0].description = "NEW FIELD {{ language }}"
    new = _validate_template(
        stored, "profile", deployment_defaults=memory_template_data(deployment)
    )

    old_prompt = SchemaPromptGenerator([old], {"language": "en"}).generate_type_descriptions()
    new_prompt = SchemaPromptGenerator([new], {"language": "en"}).generate_type_descriptions()
    assert "TYPE EN" in old_prompt and "FIELD EN" in old_prompt
    assert old_prompt == new_prompt


def test_missing_context_keeps_existing_undefined_behavior_and_does_not_recurse():
    assert render_description_template("{{ language }}", {}) == ""
    assert render_description_template("{{ language | default('en') }}", {}) == "en"
    assert render_description_template("{{ language | default('en') }}", {"language": ""}) == ""
    assert render_description_template("{{ language or 'unspecified' }}", {}) == "unspecified"
    assert (
        render_description_template("{{ (language or 'unspecified') | upper }}", {})
        == "UNSPECIFIED"
    )
    assert (
        render_description_template("{% if language is undefined %}missing{% endif %}", {})
        == "missing"
    )
    assert (
        render_description_template("{{ language }}", {"language": "{{ unknown }}"})
        == "{{ unknown }}"
    )
    assert render_description_template("  plain\n", {}, strip=False) == "  plain\n"
    assert render_description_template("  plain  ", {}) == "plain"
    assert render_description_template("", {}) == ""


@pytest.mark.parametrize("character", ["a", "中", "😀"])
def test_description_limits(character):
    text = character * MAX_DESCRIPTION_CHARS
    assert render_description_template(text, {}) == text
    with pytest.raises(DescriptionTemplateError, match="template_too_large"):
        validate_description_template(text + character)
    with pytest.raises(DescriptionTemplateError, match="output_too_large"):
        render_description_template(
            "{{ language }}{{ language }}", {"language": "x" * MAX_DESCRIPTION_OUTPUT_BYTES}
        )
    with pytest.raises(DescriptionTemplateError, match="template_too_complex"):
        validate_description_template("{{ language }}" * 2050)


def test_context_objects_never_reach_the_sandbox():
    class HostileString(str):
        def upper(self):
            pytest.fail("Untrusted method was called")

    for value in (HostileString("en"), {"upper": lambda: pytest.fail("Called mapping")}, object()):
        with pytest.raises(DescriptionTemplateError, match="invalid_context_value"):
            render_description_template("{{ language.upper() }}", {"language": value})


def test_all_deployment_descriptions_pass_restricted_rendering():
    schemas = MemoryTypeRegistry().list_all(include_disabled=True)
    for language in ("en", "zh-CN", "ja"):
        generator = SchemaModelGenerator(schemas, template_context={"language": language})
        generator.generate_all_models()
        generator.create_structured_operations_model()
        SchemaPromptGenerator(schemas, {"language": language}).generate_type_descriptions()
