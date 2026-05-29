# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

import pytest

from openviking.session.tool_result_synopsis import (
    ToolResultSynopsis,
    generate_tool_result_synopsis,
    render_tool_result_stub,
)


@pytest.fixture(autouse=True)
def _drain_background_tasks():
    yield


def test_render_stub_preserves_navigation_contract():
    synopsis = ToolResultSynopsis(
        kind="text",
        title="Text output",
        summary=["Contains 120 characters across 3 lines."],
        structure=["line_count: 3"],
        notable_items=["first line: alpha"],
        sample="alpha\nbeta\ngamma",
    )

    rendered = render_tool_result_stub(
        synopsis,
        ref="viking://session/s1/tool-results/tr_123",
        tool_name="read_file",
        sha256="0123456789abcdef0123",
        reason="single_threshold",
        original_chars=120,
        preview_chars=80,
    )

    assert "[OpenViking tool result externalized]" in rendered
    assert "kind: text" in rendered
    assert "tool_name: read_file" in rendered
    assert "original_chars: 120" in rendered
    assert "preview_chars: 80" in rendered
    assert "ref: viking://session/s1/tool-results/tr_123" in rendered
    assert "openviking_tool_result_search" in rendered
    assert "openviking_tool_result_read" in rendered


def test_json_synopsis_reports_shape():
    content = '{"users":[{"id":1,"name":"Ada"},{"id":2,"name":"Lin"}],"meta":{"count":2}}'
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "json"
    joined = "\n".join(synopsis.summary + synopsis.structure + synopsis.notable_items)
    assert "top-level keys" in joined
    assert "users" in joined
    assert "array length: 2" in joined


def test_csv_synopsis_reports_columns_and_rows():
    content = "name,score\nAda,99\nLin,98\n"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "csv"
    joined = "\n".join(synopsis.summary + synopsis.structure)
    assert "columns: name, score" in joined
    assert "rows: 2" in joined


def test_tsv_synopsis_reports_columns_and_rows():
    content = "name\tscore\nAda\t99\nLin\t98\n"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "tsv"
    joined = "\n".join(synopsis.summary + synopsis.structure)
    assert "columns: name, score" in joined
    assert "rows: 2" in joined


def test_yaml_synopsis_reports_shape():
    content = "services:\n  api:\n    image: ov:latest\n    replicas: 2\n"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "yaml"
    joined = "\n".join(synopsis.summary + synopsis.structure)
    assert "services" in joined
    assert "top-level keys" in joined


def test_xml_synopsis_reports_root_and_children():
    content = "<root><item id='1'>A</item><item id='2'>B</item></root>"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "xml"
    joined = "\n".join(synopsis.summary + synopsis.structure)
    assert "root: root" in joined
    assert "item: 2" in joined


def test_code_synopsis_reports_symbols():
    content = "import os\n\nclass Runner:\n    pass\n\ndef main():\n    return os.getcwd()\n"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "code"
    joined = "\n".join(synopsis.summary + synopsis.structure + synopsis.notable_items)
    assert "imports: os" in joined
    assert "class Runner" in joined
    assert "def main" in joined


def test_log_like_output_uses_text_synopsis_for_lossless_parity():
    content = "INFO start\nWARN retry once\nERROR failed to open file\nINFO done\n"
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "text"
    joined = "\n".join(synopsis.summary + synopsis.notable_items)
    assert "Text exploration summary" in joined
    assert "Log-like output" not in joined
    assert "ERROR failed" in joined


def test_text_synopsis_uses_deterministic_summary_shape():
    content = "# Heading\n\nSYSTEM STATUS\n\nThis is a paragraph.\n\nThis is another paragraph."
    synopsis = generate_tool_result_synopsis(content, preview_chars=200)

    assert synopsis.kind == "text"
    joined = "\n".join(
        synopsis.summary + synopsis.structure + synopsis.notable_items + [synopsis.sample]
    )
    assert "Text exploration summary" in joined
    assert "Characters:" in joined
    assert "Words:" in joined
    assert "Lines:" in joined
    assert "Detected section headers: # Heading | SYSTEM STATUS" in joined
    assert "Opening excerpt:" in joined
    assert "Closing excerpt:" in joined


def test_text_synopsis_uses_fixed_500_char_excerpts_independent_of_preview_chars():
    opening = "A" * 600
    closing = "Z" * 600
    content = f"{opening}\n\nmiddle section\n\n{closing}"

    synopsis = generate_tool_result_synopsis(content, preview_chars=10)
    joined = "\n".join(synopsis.summary)

    assert f"Opening excerpt: {'A' * 500}" in joined
    assert f"Closing excerpt: {'Z' * 500}" in joined


def test_json_synopsis_uses_fixed_shape_limits_independent_of_preview_chars():
    content = "{" + ",".join(f'"key{i}":{i}' for i in range(15)) + "}"

    synopsis = generate_tool_result_synopsis(content, preview_chars=1)
    joined = "\n".join(synopsis.summary + synopsis.structure + synopsis.notable_items)

    assert "key0" in joined
    assert "key9" in joined
    assert "key10" not in joined
    assert synopsis.sample == ""


def test_code_synopsis_uses_fixed_symbol_limits_independent_of_preview_chars():
    imports = "\n".join(f"import module_{idx}" for idx in range(15))
    defs = "\n".join(f"def function_{idx}():\n    pass" for idx in range(30))
    content = f"{imports}\n\n{defs}"

    synopsis = generate_tool_result_synopsis(content, preview_chars=1)
    joined = "\n".join(synopsis.summary + synopsis.structure + synopsis.notable_items)

    assert "module_0" in joined
    assert "module_11" in joined
    assert "module_12" not in joined
    assert "def function_23" in joined
    assert "def function_24" not in joined
    assert synopsis.sample == ""


def test_binary_like_text_uses_unknown_fallback():
    content = "\x00\x01\x02" + ("x" * 80)
    synopsis = generate_tool_result_synopsis(content, preview_chars=30)

    assert synopsis.kind == "unknown"
    assert synopsis.sample
