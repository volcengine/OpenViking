# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Run native document conversion off the service event loop."""

import threading

from openviking.parse.output import LocalParseOutputStore
from openviking.parse.parsers.anydoc import AnyDocParser
from openviking.parse.parsers.anydoc_converter import AnyDocConverter
from openviking_cli.utils.config.parser_config import AnydocConfig


async def test_anydoc_converts_a_spreadsheet_off_event_loop_with_configured_row_limit(
    tmp_path, monkeypatch
):
    source = tmp_path / "sales.csv"
    source.write_text("Product,Revenue\nApple,11\nPear,22\nPlum,33\n", encoding="utf-8")
    loop_thread = threading.get_ident()
    convert = AnyDocConverter.convert

    def observe_conversion(converter, *args, **kwargs):
        assert threading.get_ident() != loop_thread
        return convert(converter, *args, **kwargs)

    monkeypatch.setattr(AnyDocConverter, "convert", observe_conversion)
    store = LocalParseOutputStore(str(tmp_path / "artifacts"))
    parser = AnyDocParser(anydoc_config=AnydocConfig(max_table_rows=2))

    result = await parser.parse(source, parse_output_store=store)

    assert result.source_format == "csv"
    assert result.artifact_ref is not None
    artifact = await store.read_bytes(result.artifact_ref, "sales/sales.md")
    assert b"Product" in artifact and b"Apple" in artifact
    assert b"Plum" not in artifact
