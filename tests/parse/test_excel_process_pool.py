# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for ExcelParser._should_use_process_pool, the config-gated routing decision
that decides whether Excel→Markdown conversion + layout planning run in a
ProcessPoolExecutor child process instead of the main process's event loop.

A real ProcessPoolExecutor is not exercised here: spawning a child process per
test is slow and orthogonal to this decision, and the worker itself
(_build_excel_layout_in_process) is a thin, side-effect-free function that only
touches its own arguments. This covers the routing decision that gates it.
"""

from pathlib import Path

import pytest

from openviking.parse.parsers.excel import (
    ExcelParser,
    _EXCEL_PROCESS_POOL_MIN_BYTES,
)
from openviking_cli.utils.config.parser_config import (
    ExcelConfig,
    MarkdownConfig,
    ParserConfig,
)


class TestShouldUseProcessPool:
    def _parser(self, **excel_kwargs) -> ExcelParser:
        return ExcelParser(config=ExcelConfig(**excel_kwargs))

    def _make_file(self, tmp_path: Path, suffix: str = ".xlsx", size: int = 10) -> Path:
        path = tmp_path / f"sheet{suffix}"
        path.write_bytes(b"x" * size)
        return path

    def test_disabled_by_default(self, tmp_path: Path):
        path = self._make_file(tmp_path, size=_EXCEL_PROCESS_POOL_MIN_BYTES)
        assert self._parser()._should_use_process_pool(path, {}) is False

    def test_xls_never_uses_process_pool(self, tmp_path: Path):
        # Legacy .xls goes through xlrd, not the openpyxl path the worker assumes.
        path = self._make_file(tmp_path, suffix=".xls", size=_EXCEL_PROCESS_POOL_MIN_BYTES)
        assert (
            self._parser(enable_process_pool=True)._should_use_process_pool(path, {})
            is False
        )

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"enable_link_rewrite": True},
            {"base_dir": Path(".")},
            {"allowed_media_dirs": [Path(".")]},
        ],
    )
    def test_link_or_media_rewrite_kwargs_disable_process_pool(self, tmp_path, kwargs):
        # The worker never touches VikingFS/base_dir-relative media, so any parse
        # that needs link/media rewriting must stay in-process.
        path = self._make_file(tmp_path, size=_EXCEL_PROCESS_POOL_MIN_BYTES)
        assert (
            self._parser(enable_process_pool=True)._should_use_process_pool(path, kwargs)
            is False
        )

    def test_below_min_bytes_returns_false(self, tmp_path):
        path = self._make_file(tmp_path, size=_EXCEL_PROCESS_POOL_MIN_BYTES - 1)
        assert (
            self._parser(enable_process_pool=True)._should_use_process_pool(path, {})
            is False
        )

    def test_at_or_above_min_bytes_returns_true(self, tmp_path):
        path = self._make_file(tmp_path, size=_EXCEL_PROCESS_POOL_MIN_BYTES)
        assert (
            self._parser(enable_process_pool=True)._should_use_process_pool(path, {})
            is True
        )


class TestExcelConfig:
    def test_defaults(self):
        cfg = ExcelConfig()
        assert cfg.enable_process_pool is False
        assert cfg.process_pool_workers == 2

    def test_validate_rejects_zero_workers(self):
        cfg = ExcelConfig(process_pool_workers=0)
        with pytest.raises(ValueError, match="process_pool_workers"):
            cfg.validate()

    def test_openviking_config_accepts_excel_section(self):
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

        cfg = OpenVikingConfig.from_dict(
            {
                "excel": {
                    "enable_process_pool": True,
                    "process_pool_workers": 8,
                }
            }
        )
        assert cfg.excel.enable_process_pool is True
        assert cfg.excel.process_pool_workers == 8


class TestExcelSectioningInheritance:
    """Excel used to be registered with ``config.markdown``.

    A dedicated ``parsers.excel`` section must not silently change sectioning,
    because section boundaries decide node structure and stable Viking URIs.
    """

    def test_unset_sectioning_follows_markdown(self):
        markdown = MarkdownConfig(max_section_size=512, max_section_chars=2000)
        resolved = ExcelConfig().with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 512
        assert resolved.max_section_chars == 2000

    def test_explicit_excel_values_win(self):
        markdown = MarkdownConfig(max_section_size=512, max_section_chars=2000)
        resolved = ExcelConfig(max_section_size=1024).with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 1024
        # Still unset, so this one keeps following Markdown.
        assert resolved.max_section_chars == 2000

    def test_process_pool_knobs_are_never_inherited(self):
        markdown = MarkdownConfig(max_section_size=512)
        resolved = ExcelConfig(
            enable_process_pool=True, process_pool_workers=8
        ).with_sectioning_defaults_from(markdown)
        assert resolved.enable_process_pool is True
        assert resolved.process_pool_workers == 8
        assert resolved.max_section_size == 512

    def test_default_markdown_leaves_excel_unchanged(self):
        resolved = ExcelConfig().with_sectioning_defaults_from(MarkdownConfig())
        assert resolved == ExcelConfig()

    def test_missing_markdown_config_is_tolerated(self):
        assert ExcelConfig().with_sectioning_defaults_from(None) == ExcelConfig()

    def test_registry_resolves_excel_against_markdown(self, monkeypatch):
        from types import SimpleNamespace

        from openviking.parse import registry as registry_module

        config = SimpleNamespace(
            text=ParserConfig(),
            markdown=MarkdownConfig(max_section_size=512, max_section_chars=2000),
            pdf=ParserConfig(),
            html=ParserConfig(),
            excel=ExcelConfig(enable_process_pool=True),
            image=ParserConfig(),
        )
        monkeypatch.setattr(
            "openviking_cli.utils.config.get_openviking_config",
            lambda: config,
        )
        monkeypatch.setattr(registry_module, "_default_registry", None)

        excel_parser = registry_module.get_registry().get_parser_for_file("book.xlsx")

        assert excel_parser.config.enable_process_pool is True
        assert excel_parser.config.max_section_size == 512
        # The inner MarkdownParser is what actually sections the converted sheet.
        assert excel_parser._md_parser.config.max_section_size == 512
        assert excel_parser._md_parser.config.max_section_chars == 2000
