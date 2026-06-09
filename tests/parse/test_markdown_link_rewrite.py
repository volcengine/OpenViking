# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Tests for MarkdownParser relative-link rewriting on ingest."""

from pathlib import Path
from unittest.mock import patch

from openviking.parse.parsers.base_parser import BaseParser
from openviking.parse.parsers.directory import DirectoryParser
from openviking.parse.parsers.markdown import MarkdownParser


class TestRewriteRelativeLinks:
    def _parser(self) -> MarkdownParser:
        return MarkdownParser()

    def _make_tree(self, tmp_path: Path) -> Path:
        """构造与真实 knowledge 同形的小目录，返回入库根 (knowledge/)。"""
        kb = tmp_path / "knowledge"
        tgt = kb / "目录甲" / "目录乙" / "目录丙"
        tgt.mkdir(parents=True)
        (tgt / "文档.md").write_text("# 目标\n\n内容", encoding="utf-8")
        (kb / "img").mkdir()
        (kb / "img" / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (kb / "文档.md").write_text("placeholder", encoding="utf-8")
        return kb

    async def _rewrite(self, parser, kb, content, section_subpath=""):
        return await parser._rewrite_relative_links(
            content,
            source_path=str(kb / "文档.md"),
            doc_name="文档",
            section_subpath=section_subpath,
            import_root=str(kb),
        )

    async def test_md_target_becomes_directory(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "见 [x](./目录甲/目录乙/目录丙/文档.md)",
        )
        assert out == "见 [x](../目录甲/目录乙/目录丙/文档/)"

    async def test_nonempty_subpath_adds_one_more_parent(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "[x](./目录甲/目录乙/目录丙/文档.md)",
            section_subpath="二、示例小节",
        )
        assert out == "[x](../../目录甲/目录乙/目录丙/文档/)"

    async def test_image_embed_left_to_ingest(self, tmp_path: Path):
        # Image embeds (![...]) are owned entirely by #2429's _ingest_local_images;
        # link rewriting must leave them untouched.
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(self._parser(), kb, "![p](./img/a.png)")
        assert out == "![p](./img/a.png)"

    async def test_external_anchor_absolute_unchanged(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        p = self._parser()
        for link in ("https://x.com/a", "viking://resources/x", "#sec", "/abs/p.md", "mailto:a@b.c"):
            content = f"[t]({link})"
            assert await self._rewrite(p, kb, content) == content

    async def test_missing_target_unchanged(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(self._parser(), kb, "[t](./nope.md)")
        assert out == "[t](./nope.md)"

    async def test_directory_target_depth_adjusted(self, tmp_path: Path):
        # A link to a sibling directory keeps its path, but the source's added depth
        # shifts the prefix (the directory itself is translated on ingest).
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(self._parser(), kb, "[d](./img)")
        assert out == "[d](../img)"

    async def test_sibling_md_without_dot_prefix(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "[x](目录甲/目录乙/目录丙/文档.md)",
        )
        assert out == "[x](../目录甲/目录乙/目录丙/文档/)"

    async def test_target_outside_import_root_unchanged(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        (tmp_path / "outside.md").write_text("# o", encoding="utf-8")
        out = await self._rewrite(self._parser(), kb, "[t](../outside.md)")
        assert out == "[t](../outside.md)"

    async def test_fragment_kept_for_small_file(self, tmp_path: Path):
        # Small target stays a single file <dir>/<dir>.md, so its in-file #anchor
        # still resolves: point at the file and keep the fragment.
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "[x](./目录甲/目录乙/目录丙/文档.md#流程)",
        )
        assert out == "[x](../目录甲/目录乙/目录丙/文档/文档.md#流程)"

    async def test_query_suffix_kept_for_small_file(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "[x](./目录甲/目录乙/目录丙/文档.md?v=1)",
        )
        assert out == "[x](../目录甲/目录乙/目录丙/文档/文档.md?v=1)"

    async def test_large_file_anchor_located(self, tmp_path: Path):
        # Large target is split into section files; the anchor is located via an
        # in-memory parse → link points at the specific section file + keeps anchor.
        kb = self._make_tree(tmp_path)
        big = kb / "目录甲" / "目录乙" / "目录丙" / "big.md"
        body = "".join(
            f"## 第{i}章 {name}\n\n" + ("正文内容。" * 400) + "\n\n"
            for i, name in [(1, "部署"), (2, "监控"), (3, "排查")]
        )
        big.write_text(body, encoding="utf-8")
        out = await self._rewrite(
            self._parser(), kb,
            "[x](./目录甲/目录乙/目录丙/big.md#第3章-排查)",
        )
        assert out.startswith("[x](../目录甲/目录乙/目录丙/big/")
        assert out.endswith(".md#第3章-排查)")  # points at a file, anchor kept

    async def test_large_file_unlocatable_anchor_falls_back_to_dir(self, tmp_path: Path):
        # Anchor matches no heading in the (large) target → drop suffix, point at dir.
        kb = self._make_tree(tmp_path)
        big = kb / "目录甲" / "目录乙" / "目录丙" / "big.md"
        big.write_text("# 大文档\n\n" + ("这是一段较长的正文内容。" * 1200), encoding="utf-8")
        out = await self._rewrite(
            self._parser(), kb,
            "[x](./目录甲/目录乙/目录丙/big.md#不存在的章节)",
        )
        assert out == "[x](../目录甲/目录乙/目录丙/big/)"

    async def test_multiple_links_on_one_line(self, tmp_path: Path):
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(
            self._parser(), kb,
            "a [1](./目录甲/目录乙/目录丙/文档.md) b ![p](./img/a.png)",
        )
        assert out == (
            "a [1](../目录甲/目录乙/目录丙/文档/) "
            "b ![p](./img/a.png)"  # image embed left to #2429, not rewritten
        )

    async def test_future_bare_file_layout_points_at_file(self, tmp_path: Path):
        """前瞻：若 MarkdownParser 改为小 .md 不再拆成目录（in-memory parse 得到裸
        文件 layout），重写自动指向文件而非目录——落点完全由 layout 决定、无目录化假设。
        无需改 _rewrite_single_link，只要 parse_content 的产物变了就自动跟随。"""
        kb = self._make_tree(tmp_path)
        p = self._parser()

        async def fake_bare_layout(_path):  # 模拟未来：目标入库为单个裸文件，无 <dir>/ 包裹
            return {"文档.md": "# 目标\n\n内容"}

        p._target_split_files = fake_bare_layout  # type: ignore[method-assign]
        base = "./目录甲/目录乙/目录丙/文档.md"
        # 无 suffix → 文件本身（无尾斜杠），而非 文档/ 目录
        assert await self._rewrite(p, kb, f"[x]({base})") == "[x](../目录甲/目录乙/目录丙/文档.md)"
        # ?query → 文件 + 保留查询串
        assert await self._rewrite(p, kb, f"[x]({base}?v=1)") == "[x](../目录甲/目录乙/目录丙/文档.md?v=1)"
        # #anchor → 文件 + 保留锚点（裸单文件内任意锚点仍有效）
        assert await self._rewrite(p, kb, f"[x]({base}#任意)") == "[x](../目录甲/目录乙/目录丙/文档.md#任意)"


class TestSectionSubpath:
    def _parser(self) -> MarkdownParser:
        return MarkdownParser()

    def test_file_directly_under_root_is_empty(self):
        root = "viking://temp/x/文档"
        assert self._parser()._section_subpath(f"{root}/文档.md", root) == ""

    def test_file_in_subdir(self):
        root = "viking://temp/x/文档"
        uri = f"{root}/二、示例小节/sec_1.md"
        assert self._parser()._section_subpath(uri, root) == "二、示例小节"

    def test_file_in_nested_subdir(self):
        root = "viking://temp/x/文档"
        uri = f"{root}/a/b/sec.md"
        assert self._parser()._section_subpath(uri, root) == "a/b"


class FakeVikingFS:
    """Minimal VikingFS mock that records calls and supports merge ops."""

    def __init__(self):
        self.dirs = []
        self.files = {}
        self._temp_counter = 0

    async def mkdir(self, uri, exist_ok=False, **kw):
        if uri not in self.dirs:
            self.dirs.append(uri)

    async def write(self, uri, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.files[uri] = data
        return uri

    async def write_file(self, uri, content):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def write_file_bytes(self, uri, content):
        self.files[uri] = content

    async def read(self, uri, offset=0, size=-1):
        return self.files.get(uri, b"")

    async def read_file(self, uri):
        data = self.files.get(uri, b"")
        return data.decode("utf-8") if isinstance(data, bytes) else data

    async def glob(self, pattern, uri="", **kw):
        # Mirror VikingFS.glob enough for _ingest_local_images, which only ever asks
        # for "*.md" under a root: list stored files matching the pattern's suffix.
        suffix = pattern.lstrip("*")
        prefix = uri.rstrip("/") + "/"
        matches = [u for u in self.files if u.startswith(prefix) and u.endswith(suffix)]
        return {"matches": matches}

    async def ls(self, uri, node_limit=None):
        prefix = uri.rstrip("/") + "/"
        children = {}
        for key in list(self.files.keys()) + self.dirs:
            if key.startswith(prefix):
                rest = key[len(prefix):]
                if rest:
                    child_name = rest.split("/")[0]
                    is_deeper = "/" in rest[len(child_name):]
                    child_full = f"{prefix}{child_name}"
                    is_dir = children.get(child_name, False) or is_deeper or child_full in self.dirs
                    children[child_name] = is_dir
        result = []
        for name in sorted(children):
            child_uri = f"{uri.rstrip('/')}/{name}"
            result.append({
                "name": name, "uri": child_uri,
                "isDir": children[name],
                "type": "directory" if children[name] else "file",
            })
        return result

    async def move_file(self, from_uri, to_uri):
        if from_uri in self.files:
            self.files[to_uri] = self.files.pop(from_uri)

    async def delete_temp(self, temp_uri):
        prefix = temp_uri.rstrip("/") + "/"
        to_del = [k for k in self.files if k == temp_uri or k.startswith(prefix)]
        for k in to_del:
            del self.files[k]
        self.dirs = [d for d in self.dirs if d != temp_uri and not d.startswith(prefix)]

    def create_temp_uri(self):
        self._temp_counter += 1
        return f"viking://temp/md_{self._temp_counter}"


def _decode(v):
    return v.decode("utf-8") if isinstance(v, bytes) else v


class TestComputeLayoutPurity:
    """parse/write split: _compute_layout plans the VikingFS layout but writes nothing,
    so the link-rewrite in-memory probe can reuse it without a fake FS or any side effect."""

    async def test_compute_layout_plans_sections_without_touching_vikingfs(self, tmp_path: Path):
        # A multi-section document large enough to split into several section files.
        src = tmp_path / "big.md"
        body = "".join(
            f"## 第{i}章\n\n" + ("正文内容。" * 400) + "\n\n" for i in range(1, 4)
        )
        src.write_text(body, encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            parser = MarkdownParser()
            layout = await parser._compute_layout(
                parser._read_file(src), temp_uri="viking://temp/probe", source_path=str(src)
            )

        # The plan enumerates the section writes (raw content, before any rewrite)...
        writes = [op for op in layout.ops if op.kind == "write"]
        assert len(writes) >= 2, layout.ops
        assert all(op.content for op in writes)
        # ...yet nothing was ever written to VikingFS: planning is side-effect free.
        assert fake.files == {}
        assert fake.dirs == []


class TestParseContentRewiring:
    async def test_parse_content_rewrites_link_when_enabled(self, tmp_path: Path):
        kb = tmp_path / "knowledge"
        tgt = kb / "目录甲" / "目录乙" / "目录丙"
        tgt.mkdir(parents=True)
        (tgt / "文档.md").write_text("# 目标\n\n内容", encoding="utf-8")
        src = kb / "文档.md"
        src.write_text(
            "见 [x](./目录甲/目录乙/目录丙/文档.md)", encoding="utf-8"
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        written = [_decode(c) for u, c in fake.files.items() if "见" in _decode(c)]
        assert written, fake.files
        assert "../目录甲/目录乙/目录丙/文档/" in written[0]

    async def test_parse_content_no_rewrite_when_disabled(self, tmp_path: Path):
        kb = tmp_path / "knowledge"
        tgt = kb / "目录甲" / "目录乙" / "目录丙"
        tgt.mkdir(parents=True)
        (tgt / "文档.md").write_text("# 目标\n\n内容", encoding="utf-8")
        src = kb / "文档.md"
        src.write_text(
            "见 [x](./目录甲/目录乙/目录丙/文档.md)", encoding="utf-8"
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(str(src))  # rewrite disabled by default

        written = [_decode(c) for u, c in fake.files.items() if "见" in _decode(c)]
        assert written, fake.files
        assert "./目录甲/目录乙/目录丙/文档.md" in written[0]

    async def test_no_rewrite_without_import_root(self, tmp_path: Path):
        # enable_link_rewrite=True but no link_rewrite_root (the single-file path):
        # without an ingest root there is nothing to bound against, so do NOT rewrite.
        kb = tmp_path / "knowledge"
        tgt = kb / "目录甲" / "目录乙" / "目录丙"
        tgt.mkdir(parents=True)
        (tgt / "文档.md").write_text("# 目标\n\n内容", encoding="utf-8")
        src = kb / "文档.md"
        src.write_text(
            "见 [x](./目录甲/目录乙/目录丙/文档.md)", encoding="utf-8"
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(str(src), enable_link_rewrite=True)

        written = [_decode(c) for u, c in fake.files.items() if "见" in _decode(c)]
        assert written, fake.files
        assert "./目录甲/目录乙/目录丙/文档.md" in written[0]


class TestDirectoryEndToEnd:
    async def test_directory_ingest_rewrites_cross_file_link(self, tmp_path: Path):
        kb = tmp_path / "knowledge"
        tgt = kb / "目录甲" / "目录乙" / "目录丙"
        tgt.mkdir(parents=True)
        (tgt / "文档.md").write_text("# 目标\n\n内容", encoding="utf-8")
        (kb / "文档.md").write_text(
            "见 [x](./目录甲/目录乙/目录丙/文档.md)", encoding="utf-8"
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await DirectoryParser().parse(str(kb))

        written = [_decode(c) for c in fake.files.values() if "见" in _decode(c)]
        assert written, fake.files
        assert "../目录甲/目录乙/目录丙/文档/" in written[0]

    async def test_directory_flat_mode_does_not_rewrite(self, tmp_path: Path):
        # preserve_structure=False -> rewrite disabled -> links left untouched.
        kb = tmp_path / "knowledge"
        sub = kb / "sub"
        sub.mkdir(parents=True)
        (sub / "target.md").write_text("# 目标\n\n内容", encoding="utf-8")
        (kb / "root.md").write_text("见 [x](./sub/target.md)", encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await DirectoryParser().parse(str(kb), preserve_structure=False)

        written = [_decode(c) for c in fake.files.values() if "见" in _decode(c)]
        assert written, fake.files
        assert "./sub/target.md" in written[0]
