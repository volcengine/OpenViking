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

from openviking.parse.parsers import excel as excel_module
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


@pytest.mark.asyncio
async def test_process_pool_forwards_no_split_layout_flag(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    path = tmp_path / "sheet.xlsx"
    path.write_bytes(b"xlsx")
    parser = ExcelParser(config=ExcelConfig(enable_process_pool=True))
    monkeypatch.setattr(
        parser._md_parser,
        "_create_temp_uri",
        lambda: "viking://temp/test/excel",
    )
    captured = {}

    class _CapturingLoop:
        def run_in_executor(self, _executor, callback):
            captured["callback"] = callback
            raise RuntimeError("layout callback captured")

    monkeypatch.setattr(excel_module.asyncio, "get_running_loop", lambda: _CapturingLoop())
    monkeypatch.setattr(excel_module, "_get_excel_layout_executor", lambda _workers: object())

    with pytest.raises(RuntimeError, match="layout callback captured"):
        await parser._parse_existing_path_process_pool(path, split_content=False)

    callback = captured["callback"]
    assert callback.keywords["layout_kwargs"]["split_content"] is False


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
        resolved = ExcelConfig.from_dict({}).with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 512
        assert resolved.max_section_chars == 2000

    def test_explicit_excel_values_win(self):
        markdown = MarkdownConfig(max_section_size=512, max_section_chars=2000)
        resolved = ExcelConfig.from_dict(
            {"max_section_size": 1024}
        ).with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 1024
        # Absent from parsers.excel, so this one keeps following Markdown.
        assert resolved.max_section_chars == 2000

    def test_explicit_value_equal_to_default_is_not_overwritten(self):
        """"Excel keeps 2048" is a legitimate config even though 2048 is the default.

        Comparing against class defaults cannot distinguish it from an absent
        key, which would make the documented "explicit values win" contract
        false for exactly this case.
        """
        markdown = MarkdownConfig(max_section_size=512)
        resolved = ExcelConfig.from_dict(
            {"max_section_size": 2048}
        ).with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 2048

    def test_process_pool_knobs_are_never_inherited(self):
        markdown = MarkdownConfig(max_section_size=512)
        resolved = ExcelConfig.from_dict(
            {"enable_process_pool": True, "process_pool_workers": 8}
        ).with_sectioning_defaults_from(markdown)
        assert resolved.enable_process_pool is True
        assert resolved.process_pool_workers == 8
        assert resolved.max_section_size == 512

    def test_default_markdown_leaves_excel_unchanged(self):
        resolved = ExcelConfig.from_dict({}).with_sectioning_defaults_from(MarkdownConfig())
        assert resolved == ExcelConfig()

    def test_missing_markdown_config_is_tolerated(self):
        assert ExcelConfig.from_dict({}).with_sectioning_defaults_from(None) == ExcelConfig()

    def test_hand_built_config_is_treated_as_fully_explicit(self):
        """A config built without from_dict carries no key provenance."""
        markdown = MarkdownConfig(max_section_size=512)
        resolved = ExcelConfig(max_section_size=2048).with_sectioning_defaults_from(markdown)
        assert resolved.max_section_size == 2048

    def test_provenance_does_not_affect_equality(self):
        assert ExcelConfig.from_dict({}) == ExcelConfig()
        assert ExcelConfig.from_dict({"max_section_size": 2048}) == ExcelConfig()

    def test_absent_excel_section_still_inherits_through_full_config(self):
        """Deployments predating parsers.excel must keep their Excel sectioning."""
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

        cfg = OpenVikingConfig.from_dict({"markdown": {"max_section_size": 512}})
        resolved = cfg.excel.with_sectioning_defaults_from(cfg.markdown)
        assert resolved.max_section_size == 512

    def test_key_provenance_does_not_leak_into_serialization(self):
        """Provenance is bookkeeping, not config: it must not reach any output.

        A dataclass field would appear in asdict/model_dump and, being a
        frozenset, would break JSON serialization for callers.
        """
        import dataclasses
        import json

        cfg = ExcelConfig.from_dict({"max_section_size": 2048})
        dumped = dataclasses.asdict(cfg)
        assert "_explicit_fields" not in dumped
        assert not any(key.startswith("_") for key in dumped), sorted(dumped)
        json.dumps(dumped)

    def test_config_data_cannot_forge_key_provenance(self):
        """Provenance must not be settable from a config file."""
        with pytest.raises(ValueError):
            ExcelConfig.from_dict({"_explicit_fields": ["max_section_size"]})

    def test_replace_and_copy_preserve_key_provenance(self):
        import copy
        import dataclasses

        markdown = MarkdownConfig(max_section_size=512)
        cfg = ExcelConfig.from_dict({"max_section_size": 2048})

        for variant in (
            dataclasses.replace(cfg, enable_process_pool=True),
            copy.copy(cfg),
            copy.deepcopy(cfg),
        ):
            resolved = variant.with_sectioning_defaults_from(markdown)
            assert resolved.max_section_size == 2048

    def test_absent_excel_section_inherits_through_parser_loader(self):
        """load_parser_configs_from_dict is a second config entry point."""
        from openviking_cli.utils.config.parser_config import (
            load_parser_configs_from_dict,
        )

        configs = load_parser_configs_from_dict({"markdown": {"max_section_size": 512}})
        resolved = configs["excel"].with_sectioning_defaults_from(configs["markdown"])
        assert resolved.max_section_size == 512

    def test_explicit_excel_section_wins_through_full_config(self):
        from openviking_cli.utils.config.open_viking_config import OpenVikingConfig

        cfg = OpenVikingConfig.from_dict(
            {
                "markdown": {"max_section_size": 512},
                "excel": {"max_section_size": 2048, "enable_process_pool": True},
            }
        )
        resolved = cfg.excel.with_sectioning_defaults_from(cfg.markdown)
        assert resolved.max_section_size == 2048
        assert resolved.enable_process_pool is True

    def test_registry_resolves_excel_against_markdown(self, monkeypatch):
        from types import SimpleNamespace

        from openviking.parse import registry as registry_module

        config = SimpleNamespace(
            text=ParserConfig(),
            markdown=MarkdownConfig(max_section_size=512, max_section_chars=2000),
            pdf=ParserConfig(),
            html=ParserConfig(),
            excel=ExcelConfig.from_dict({"enable_process_pool": True}),
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
