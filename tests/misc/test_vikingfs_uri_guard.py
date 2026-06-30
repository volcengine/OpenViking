# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Regression tests for traversal-style URI rejection in VikingFS."""

import contextvars
from unittest.mock import AsyncMock, MagicMock

import pytest

from openviking.pyagfs.exceptions import AGFSInvalidOperationError
from openviking.server.identity import RequestContext, Role
from openviking.storage.viking_fs import VikingFS
from openviking_cli.exceptions import PermissionDeniedError
from openviking_cli.session.user_id import UserIdentifier


def _make_viking_fs() -> VikingFS:
    """Create a VikingFS instance with a mocked AGFS backend."""
    fs = VikingFS.__new__(VikingFS)
    fs.agfs = AsyncMock()
    fs._async_agfs = fs.agfs
    fs.query_embedder = None
    fs.rerank_config = None
    fs.grep_config = None
    fs.vector_store = None
    fs._encryptor = None
    fs._bound_ctx = contextvars.ContextVar("vikingfs_bound_ctx_test", default=None)
    return fs


def _user_ctx(account_id: str = "acct1", user_id: str = "alice") -> RequestContext:
    return RequestContext(
        user=UserIdentifier(account_id=account_id, user_id=user_id),
        role=Role.USER,
    )


class TestVikingFSURITraversalGuard:
    """Traversal-style URI components should be rejected before any AGFS I/O."""

    @pytest.mark.parametrize(
        ("base_path", "match_file", "expected"),
        [
            ("/local/default/resources/docs", ".", "/local/default/resources/docs"),
            (
                "/local/default/resources/docs",
                "sub/a.md",
                "/local/default/resources/docs/sub/a.md",
            ),
        ],
    )
    def test_resolve_grep_match_agfs_path(
        self, base_path: str, match_file: str, expected: str
    ) -> None:
        fs = _make_viking_fs()

        assert fs._resolve_grep_match_agfs_path(base_path, match_file) == expected

    @pytest.mark.parametrize(
        "uri",
        [
            "viking://resources/../_system/users.json",
            "viking://resources/../../_system/accounts.json",
            "/resources/../_system/users.json",
            "viking://resources/..\\..\\_system\\users.json",
            "viking://resources/C:\\Windows\\System32",
        ],
    )
    def test_rejects_unsafe_uri_components(self, uri: str) -> None:
        fs = _make_viking_fs()

        with pytest.raises(PermissionDeniedError, match="Unsafe URI"):
            fs._normalized_uri_parts(uri)

    @pytest.mark.asyncio
    async def test_read_file_rejects_traversal_before_agfs_read(self) -> None:
        fs = _make_viking_fs()

        with pytest.raises(PermissionDeniedError, match="Unsafe URI"):
            await fs.read_file("viking://resources/../_system/users.json")

        fs.agfs.read.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_rejects_traversal_before_agfs_write(self) -> None:
        fs = _make_viking_fs()

        with pytest.raises(PermissionDeniedError, match="Unsafe URI"):
            await fs.write("viking://resources/../../_system/accounts.json", "pwned")

        fs.agfs.write.assert_not_called()

    @pytest.mark.asyncio
    async def test_rm_rejects_traversal_before_side_effects(self) -> None:
        fs = _make_viking_fs()
        fs._collect_uris = AsyncMock(return_value=[])
        fs._delete_from_vector_store = AsyncMock()

        with pytest.raises(PermissionDeniedError, match="Unsafe URI"):
            await fs.rm("viking://resources/../../other_account/_system/users.json")

        fs._collect_uris.assert_not_called()
        fs._delete_from_vector_store.assert_not_called()
        fs.agfs.rm.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "uri",
        [
            "viking://",
            "/",
            "viking://user",
            "viking://user/",
            "/user",
            "user",
        ],
    )
    async def test_rm_rejects_protected_namespace_roots_before_side_effects(
        self, uri: str
    ) -> None:
        fs = _make_viking_fs()
        fs._collect_uris = AsyncMock(return_value=[])
        fs._delete_from_vector_store = AsyncMock()

        with pytest.raises(PermissionDeniedError, match="Deleting viking://"):
            await fs.rm(uri, recursive=True)

        fs._collect_uris.assert_not_called()
        fs._delete_from_vector_store.assert_not_called()
        fs.agfs.stat.assert_not_called()
        fs.agfs.rm.assert_not_called()

    @pytest.mark.parametrize("uri", ["viking://resources", "viking://session"])
    @pytest.mark.asyncio
    async def test_rm_allows_public_scope_roots_to_reach_agfs(self, uri: str) -> None:
        fs = _make_viking_fs()
        fs.agfs.stat = AsyncMock(side_effect=RuntimeError("sentinel"))

        with pytest.raises(RuntimeError, match="sentinel"):
            await fs.rm(uri, recursive=True)

        fs.agfs.stat.assert_called_once()

    @pytest.mark.asyncio
    async def test_rm_session_root_is_scoped_to_current_user_before_agfs(self) -> None:
        fs = _make_viking_fs()
        fs.agfs.stat = AsyncMock(side_effect=RuntimeError("sentinel"))

        with pytest.raises(RuntimeError, match="sentinel"):
            await fs.rm("viking://session", recursive=True, ctx=_user_ctx())

        fs.agfs.stat.assert_called_once_with("/local/acct1/user/alice/sessions")

    @pytest.mark.asyncio
    async def test_rm_rejects_temp_root_for_non_root_before_side_effects(self) -> None:
        fs = _make_viking_fs()
        fs._collect_uris = AsyncMock(return_value=[])
        fs._delete_from_vector_store = AsyncMock()

        with pytest.raises(PermissionDeniedError, match="Temp root is read-only"):
            await fs.rm("viking://temp", recursive=True, ctx=_user_ctx())

        fs._collect_uris.assert_not_called()
        fs._delete_from_vector_store.assert_not_called()
        fs.agfs.stat.assert_not_called()
        fs.agfs.rm.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("old_uri", "new_uri"),
        [
            ("viking://resources/../_system/users.json", "viking://resources/safe.txt"),
            ("viking://resources/safe.txt", "viking://resources/../../victim/_system/users.json"),
        ],
    )
    async def test_mv_rejects_traversal_in_source_or_target(
        self, old_uri: str, new_uri: str
    ) -> None:
        fs = _make_viking_fs()
        fs._collect_uris = AsyncMock(return_value=[])
        fs._update_vector_store_uris = AsyncMock()
        fs._delete_from_vector_store = AsyncMock()

        with pytest.raises(PermissionDeniedError, match="Unsafe URI"):
            await fs.mv(old_uri, new_uri)

        fs._collect_uris.assert_not_called()
        fs._update_vector_store_uris.assert_not_called()
        fs._delete_from_vector_store.assert_not_called()
        fs.agfs.mv.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("source_uri", "match"),
        [
            # Bare account root: the net-new gap — rm() rejects it (#2873) but mv()'s
            # write guard alone used to let it through to the recursive source delete.
            ("viking://", "Deleting viking://"),
            ("/", "Deleting viking://"),
            # viking://user is already blocked by the write guard; assert mv still
            # refuses it (parity with rm) regardless of which guard fires first.
            ("viking://user", "viking://user is not supported"),
            ("viking://user/", "viking://user is not supported"),
            ("/user", "viking://user is not supported"),
            ("user", "viking://user is not supported"),
        ],
    )
    async def test_mv_rejects_protected_namespace_roots_in_source_before_side_effects(
        self, source_uri: str, match: str
    ) -> None:
        fs = _make_viking_fs()
        fs._collect_uris = AsyncMock(return_value=[])
        fs._update_vector_store_uris = AsyncMock()
        fs._delete_from_vector_store = AsyncMock()

        with pytest.raises(PermissionDeniedError, match=match):
            await fs.mv(source_uri, "viking://temp/dst")

        fs._collect_uris.assert_not_called()
        fs._update_vector_store_uris.assert_not_called()
        fs._delete_from_vector_store.assert_not_called()
        fs.agfs.stat.assert_not_called()
        fs.agfs.rm.assert_not_called()
        fs.agfs.mv.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_file_keeps_valid_uri_behavior(self) -> None:
        fs = _make_viking_fs()
        fs.agfs.stat = AsyncMock(return_value=MagicMock())
        fs.agfs.read = AsyncMock(return_value=b"hello")

        content = await fs.read_file("viking://resources/docs/guide.md")
        assert content == "hello"
        fs.agfs.stat.assert_called_once_with("/local/default/resources/docs/guide.md")
        fs.agfs.read.assert_called_once_with("/local/default/resources/docs/guide.md")

    @pytest.mark.asyncio
    async def test_grep_propagates_agfs_errors_instead_of_falling_back(self) -> None:
        fs = _make_viking_fs()
        fs._encryptor = None
        fs._ensure_access = MagicMock()
        fs._grep_with_agfs = AsyncMock(side_effect=AGFSInvalidOperationError("invalid regex"))
        fs._grep_encrypted = AsyncMock(
            return_value={"matches": [], "count": 0, "match_count": 0, "files_scanned": 0}
        )

        with pytest.raises(AGFSInvalidOperationError, match="invalid regex"):
            await fs.grep("viking://resources/docs", "(")

        fs._grep_with_agfs.assert_awaited_once()
        fs._grep_encrypted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_grep_with_agfs_maps_query_root_relative_match_to_uri(self) -> None:
        """Query-root-relative grep matches should be reconstructed into the final Viking URI."""
        fs = _make_viking_fs()
        fs._encryptor = None
        fs._ensure_access = MagicMock()
        fs.agfs.grep = AsyncMock(
            return_value={
                "matches": [
                    {
                        "file": "sub/a.txt",
                        "line_number": 3,
                        "content": "act",
                    }
                ],
                "count": 1,
            }
        )

        result = await fs._grep_with_agfs("viking://resources/test-root", "act")

        assert result["count"] == 1
        assert result["matches"][0]["uri"] == "viking://resources/test-root/sub/a.txt"
        assert result["matches"][0]["line"] == 3
        assert result["matches"][0]["content"] == "act"

    @pytest.mark.asyncio
    async def test_grep_with_agfs_maps_dot_match_to_query_root_uri(self) -> None:
        """A '.' grep match should resolve back to the queried Viking URI itself."""
        fs = _make_viking_fs()
        fs._encryptor = None
        fs._ensure_access = MagicMock()
        fs.agfs.grep = AsyncMock(
            return_value={
                "matches": [
                    {
                        "file": ".",
                        "line_number": 1,
                        "content": "act",
                    }
                ],
                "count": 1,
            }
        )

        result = await fs._grep_with_agfs("viking://resources/test-root", "act")

        assert result["count"] == 1
        assert result["matches"][0]["uri"] == "viking://resources/test-root"
        assert result["matches"][0]["line"] == 1
        assert result["matches"][0]["content"] == "act"

    @pytest.mark.asyncio
    async def test_ls_entries_filters_account_root_to_listable_scopes(self) -> None:
        fs = _make_viking_fs()
        fs.agfs.ls.return_value = [
            {"name": "resources", "isDir": True},
            {"name": "tasks", "isDir": True},
            {"name": "_system", "isDir": True},
        ]

        entries = await fs._ls_entries("/local/default")

        assert [entry["name"] for entry in entries] == ["resources"]
