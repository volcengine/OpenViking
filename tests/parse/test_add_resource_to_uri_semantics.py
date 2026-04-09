from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_ctx():
    from openviking.server.identity import RequestContext, Role
    from openviking_cli.session.user_id import UserIdentifier

    return RequestContext(user=UserIdentifier("default", "default", "default"), role=Role.USER)


def _make_viking_fs_mock(ls_map: dict[str, list[dict]]):
    fs = MagicMock()

    async def _ls(uri: str, **kwargs):
        return ls_map.get(uri, [])

    fs.ls = AsyncMock(side_effect=_ls)
    fs.stat = AsyncMock(return_value={"isDir": True})
    return fs


class TestAddResourceToUriSemantics:
    @pytest.mark.asyncio
    async def test_file_resource_to_dir_semantics_rule1(self):
        from openviking.parse.tree_builder import TreeBuilder

        temp_base = "viking://temp/t1"
        temp_doc = f"{temp_base}/doc"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "doc", "isDir": True}],
                temp_doc: [{"name": "a.txt", "isDir": False}],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path=temp_base,
                ctx=_make_ctx(),
                scope="resources",
                to_uri="viking://resources/mydir/",
            )

        assert tree.root is not None
        assert tree.root.uri == "viking://resources/mydir/a.txt"
        assert tree.root.temp_uri == f"{temp_doc}/a.txt"
        assert tree.root.meta.get("artifact_kind") == "file"

    @pytest.mark.asyncio
    async def test_directory_resource_to_dir_semantics_rule3(self):
        from openviking.parse.tree_builder import TreeBuilder

        temp_base = "viking://temp/t2"
        temp_doc = f"{temp_base}/myproj"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "myproj", "isDir": True}],
                temp_doc: [
                    {"name": "README.md", "isDir": False},
                    {"name": "src", "isDir": True},
                ],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path=temp_base,
                ctx=_make_ctx(),
                scope="resources",
                to_uri="viking://resources/target/",
            )

        assert tree.root is not None
        assert tree.root.uri == "viking://resources/target/myproj/"
        assert tree.root.temp_uri == temp_doc
        assert tree.root.meta.get("artifact_kind") == "dir"

    @pytest.mark.asyncio
    async def test_directory_resource_to_no_trailing_slash_rule4(self):
        from openviking.parse.tree_builder import TreeBuilder

        temp_base = "viking://temp/t3"
        temp_doc = f"{temp_base}/myproj"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "myproj", "isDir": True}],
                temp_doc: [{"name": "README.md", "isDir": False}, {"name": "src", "isDir": True}],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path=temp_base,
                ctx=_make_ctx(),
                scope="resources",
                to_uri="viking://resources/target",
            )

        assert tree.root is not None
        assert tree.root.uri == "viking://resources/target"
        assert tree.root.temp_uri == temp_doc

    @pytest.mark.asyncio
    async def test_force_directory_for_directory_source_even_single_file(self):
        from openviking.parse.tree_builder import TreeBuilder

        temp_base = "viking://temp/t6"
        temp_doc = f"{temp_base}/tt_a"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "tt_a", "isDir": True}],
                temp_doc: [{"name": "aa.md", "isDir": False}],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            tree = await builder.finalize_from_temp(
                temp_dir_path=temp_base,
                ctx=_make_ctx(),
                scope="resources",
                to_uri="viking://resources/",
                source_format="directory",
            )

        assert tree.root is not None
        assert tree.root.uri == "viking://resources/tt_a/"
        assert tree.root.temp_uri == temp_doc
        assert tree.root.meta.get("artifact_kind") == "dir"

    @pytest.mark.asyncio
    async def test_protect_resources_root_file_rule6(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking_cli.exceptions import InvalidArgumentError

        temp_base = "viking://temp/t4"
        temp_doc = f"{temp_base}/doc"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "doc", "isDir": True}],
                temp_doc: [{"name": "a.txt", "isDir": False}],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            with pytest.raises(InvalidArgumentError):
                await builder.finalize_from_temp(
                    temp_dir_path=temp_base,
                    ctx=_make_ctx(),
                    scope="resources",
                    to_uri="viking://resources",
                )

    @pytest.mark.asyncio
    async def test_protect_resources_root_dir_rule6(self):
        from openviking.parse.tree_builder import TreeBuilder
        from openviking_cli.exceptions import InvalidArgumentError

        temp_base = "viking://temp/t5"
        temp_doc = f"{temp_base}/myproj"
        fs = _make_viking_fs_mock(
            {
                temp_base: [{"name": "myproj", "isDir": True}],
                temp_doc: [{"name": "README.md", "isDir": False}, {"name": "src", "isDir": True}],
            }
        )

        builder = TreeBuilder()
        with patch("openviking.parse.tree_builder.get_viking_fs", return_value=fs):
            with pytest.raises(InvalidArgumentError):
                await builder.finalize_from_temp(
                    temp_dir_path=temp_base,
                    ctx=_make_ctx(),
                    scope="resources",
                    to_uri="viking://resources",
                )
