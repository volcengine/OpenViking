# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

from types import SimpleNamespace

from openviking.core.context import ContextLevel
from openviking.server.identity import RequestContext, Role
from openviking.storage.index_source import SourceState, build_index_sources, directory_source
from openviking_cli.session.user_id import UserIdentifier


class _FS:
    def __init__(self, values: dict[str, str], failures: set[str] | None = None) -> None:
        self.values = values
        self.failures = failures or set()

    async def exists(self, uri: str, *, ctx) -> bool:
        if uri in self.failures:
            raise OSError("unavailable")
        return uri in self.values

    async def read_file(self, uri: str, *, ctx) -> str:
        if uri in self.failures:
            raise OSError("unavailable")
        return self.values[uri]


def _ctx() -> RequestContext:
    return RequestContext(UserIdentifier("account", "user"), Role.ROOT)


async def test_directory_l1_uses_abstract_fallback() -> None:
    fs = _FS({"viking://resources/x/.abstract.md": "abstract"})
    source = await directory_source(fs, "viking://resources/x", int(ContextLevel.OVERVIEW), _ctx())
    assert source.state == SourceState.FOUND
    assert source.text == "abstract"


async def test_build_index_sources_preserves_unreadable(monkeypatch) -> None:
    monkeypatch.setattr(
        "openviking.storage.index_source.get_openviking_config",
        lambda: SimpleNamespace(
            embedding=SimpleNamespace(text_source="content", max_input_tokens=1000)
        ),
    )
    fs = _FS(
        {"viking://resources/.abstract.md": "root"},
        {"viking://resources/broken.md"},
    )
    entries = [
        {
            "uri": "viking://resources/broken.md",
            "rel_path": "broken.md",
            "name": "broken.md",
            "isDir": False,
            "size": 10,
        }
    ]
    facts, unresolved = await build_index_sources(fs, "viking://resources", entries, _ctx())
    assert {(fact.uri, fact.level) for fact in facts} == {
        ("viking://resources", 0),
        ("viking://resources", 1),
    }
    assert [(item.uri, item.level, item.reason_code) for item in unresolved] == [
        ("viking://resources/broken.md", 2, "source_read_failed")
    ]
