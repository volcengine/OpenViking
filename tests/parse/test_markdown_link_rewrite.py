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

    async def test_image_not_ingestable_depth_adjusted(self, tmp_path: Path):
        # img/a.png is a stub (no decodable pixels) so #2429's _ingest_local_images
        # will not take it; the rewrite must then depth-adjust it like any relative
        # link so it stays valid after the doc moves into its directory.
        kb = self._make_tree(tmp_path)
        out = await self._rewrite(self._parser(), kb, "![p](./img/a.png)")
        assert out == "![p](../img/a.png)"

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
            "b ![p](../img/a.png)"  # stub image: not ingestable -> depth-adjusted
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

    async def write_file(self, uri, content, **kw):
        if isinstance(content, str):
            content = content.encode("utf-8")
        self.files[uri] = content

    async def write_file_bytes(self, uri, content):
        self.files[uri] = content

    async def read(self, uri, offset=0, size=-1):
        return self.files.get(uri, b"")

    async def read_file(self, uri, **kw):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        data = self.files[uri]
        return data.decode("utf-8") if isinstance(data, bytes) else data

    async def glob(self, pattern, uri="", **kw):
        # Mirror VikingFS.glob enough for _ingest_local_images, which only ever asks
        # for "*.md" under a root: list stored files matching the pattern's suffix.
        suffix = pattern.lstrip("*")
        prefix = uri.rstrip("/") + "/"
        matches = [u for u in self.files if u.startswith(prefix) and u.endswith(suffix)]
        return {"matches": matches}

    async def rm(self, uri, **kw):
        self.files.pop(uri, None)

    async def stat(self, uri, **kw):
        if uri not in self.files:
            raise FileNotFoundError(uri)
        return {"name": uri.rsplit("/", 1)[-1], "isDir": False}

    async def ls(self, uri, node_limit=None, show_all_hidden=False, **kw):
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
            # Mirror VikingFS._ls_original: hidden FILES are filtered unless
            # show_all_hidden is set; directories are always listed.
            if not children[name] and name.startswith(".") and not show_all_hidden:
                continue
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


def _write_valid_png(path: Path, size=(20, 20)) -> None:
    from PIL import Image

    Image.new("RGB", size, color=(120, 30, 30)).save(path)


class TestImageLinkSplit:
    """Ownership split for image embeds during link rewriting:

    - Images that #2429's _ingest_local_images WILL take (resolvable within
      base_dir/allowed_media_dirs and passing validation) are left untouched —
      ingestion copies them next to the section and rewrite_image_uris later
      turns them into viking:// URIs.
    - Images it will NOT take (outside base_dir, missing, or failing validation)
      get the same depth adjustment as document links so the relative path stays
      valid after the doc moves into its ingest directory.
    """

    async def test_ingestable_image_left_untouched_and_copied(self, tmp_path: Path):
        kb = tmp_path / "kb"
        (kb / "img").mkdir(parents=True)
        _write_valid_png(kb / "img" / "photo.png")
        src = kb / "page.md"
        src.write_text("![p](./img/photo.png)", encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        md = [_decode(c) for u, c in fake.files.items() if u.endswith(".md")]
        assert md and "![p](./img/photo.png)" in md[0], fake.files
        assert any(u.endswith("/photo.png") for u in fake.files), fake.files
        assert any(u.endswith(".image_mappings.json") for u in fake.files), fake.files

    async def test_image_outside_base_dir_depth_adjusted(self, tmp_path: Path):
        # The md lives in kb/sub; the image lives in kb/img — outside base_dir
        # (= the md's own directory) but inside the import root. #2429 cannot
        # take it, so the link must be depth-adjusted to stay valid.
        kb = tmp_path / "kb"
        (kb / "img").mkdir(parents=True)
        (kb / "sub").mkdir()
        _write_valid_png(kb / "img" / "photo.png")
        src = kb / "sub" / "page.md"
        src.write_text("![p](../img/photo.png)", encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        md = [_decode(c) for u, c in fake.files.items() if u.endswith(".md")]
        assert md and "![p](../../img/photo.png)" in md[0], fake.files
        assert not any(u.endswith("/photo.png") for u in fake.files), fake.files

    async def test_html_img_ingested_and_left_for_rewrite(self, tmp_path: Path):
        # HTML <img src="..."> embeds get the same treatment as ![...] embeds:
        # ingestable ones are copied next to the section and recorded in the
        # mapping sidecar, with the tag itself left untouched.
        kb = tmp_path / "kb"
        (kb / "img").mkdir(parents=True)
        _write_valid_png(kb / "img" / "photo.png")
        src = kb / "page.md"
        src.write_text(
            '<img src="./img/photo.png" width="80%">', encoding="utf-8"
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        md = [_decode(c) for u, c in fake.files.items() if u.endswith(".md")]
        assert md and '<img src="./img/photo.png" width="80%">' in md[0], fake.files
        assert any(u.endswith("/photo.png") for u in fake.files), fake.files
        mapping = [
            _decode(c)
            for u, c in fake.files.items()
            if u.endswith(".image_mappings.json")
        ]
        assert mapping and "./img/photo.png" in mapping[0], fake.files

    async def test_html_img_outside_import_root_depth_adjusted(self, tmp_path: Path):
        # An <img> pointing outside base_dir (and no allowed_media_dirs) is not
        # ingestable -> it gets the same depth adjustment as document links,
        # other attributes preserved.
        kb = tmp_path / "kb"
        (kb / "img").mkdir(parents=True)
        (kb / "sub").mkdir()
        _write_valid_png(kb / "img" / "photo.png")
        src = kb / "sub" / "page.md"
        src.write_text('<img src="../img/photo.png" width="80%">', encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        md = [_decode(c) for u, c in fake.files.items() if u.endswith(".md")]
        assert md and '<img src="../../img/photo.png" width="80%">' in md[0], fake.files

    async def test_directory_ingest_image_within_import_root_becomes_viking(
        self, tmp_path: Path
    ):
        # Directory ingest passes the import root as allowed_media_dirs, so an
        # image outside the md's own dir but inside the ingested tree IS taken:
        # copied next to the section and rewritten to a viking:// URI.
        from openviking.parse.image_rewrite import rewrite_image_uris

        kb = tmp_path / "kb"
        (kb / "images").mkdir(parents=True)
        (kb / "guides").mkdir()
        _write_valid_png(kb / "images" / "photo.png")
        (kb / "guides" / "page.md").write_text(
            "![p](../images/photo.png)\n\n"
            '<img src="../images/photo.png" width="80%">',
            encoding="utf-8",
        )

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            result = await DirectoryParser().parse(str(kb))

            temp = result.temp_dir_path
            entries = await fake.ls(temp)
            doc_dirs = [e for e in entries if e["isDir"]]
            root = f"viking://resources/{doc_dirs[0]['name']}"
            src_prefix = doc_dirs[0]["uri"].rstrip("/") + "/"
            for u in list(fake.files):
                if u.startswith(src_prefix):
                    fake.files[f"{root}/{u[len(src_prefix):]}"] = fake.files[u]

            import openviking.parse.image_rewrite as image_rewrite_mod

            with patch.object(image_rewrite_mod, "get_viking_fs", return_value=fake):
                stats = await rewrite_image_uris(root, lock_handle=None)

        assert stats["references_rewritten"] == 2, stats
        page = _decode(fake.files[f"{root}/guides/page/page.md"])
        assert f"![p]({root}/guides/page/photo.png)" in page, page
        assert f'<img src="{root}/guides/page/photo.png" width="80%">' in page, page

    async def test_invalid_image_depth_adjusted(self, tmp_path: Path):
        # In base_dir but not a decodable image -> ingest skips it -> depth-adjust.
        kb = tmp_path / "kb"
        (kb / "img").mkdir(parents=True)
        (kb / "img" / "bad.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        src = kb / "page.md"
        src.write_text("![p](./img/bad.png)", encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            await MarkdownParser().parse(
                str(src), enable_link_rewrite=True, link_rewrite_root=str(kb)
            )

        md = [_decode(c) for u, c in fake.files.items() if u.endswith(".md")]
        assert md and "![p](../img/bad.png)" in md[0], fake.files
        assert not any(u.endswith(".image_mappings.json") for u in fake.files), fake.files


class TestRewriteImageUris:
    """rewrite_image_uris must consume every .image_mappings.json in the tree,
    interpreting each one in the coordinate system of the directory holding it
    (the doc root the parser wrote it to), not only at the resource root."""

    def _patched(self, fake):
        import openviking.parse.image_rewrite as image_rewrite_mod

        return patch.object(image_rewrite_mod, "get_viking_fs", return_value=fake)

    async def test_nested_mapping_consumed(self):
        from openviking.parse.image_rewrite import rewrite_image_uris

        root = "viking://resources/res"
        fake = FakeVikingFS()
        fake.files = {
            f"{root}/index/index.md": "![p](./assets/logo.png)".encode(),
            f"{root}/index/logo.png": b"img",
            f"{root}/index/.image_mappings.json": (
                '{"index.md": {"./assets/logo.png": "logo.png"}}'.encode()
            ),
        }

        with self._patched(fake):
            stats = await rewrite_image_uris(root, lock_handle=None)

        assert stats == {"files_processed": 1, "references_rewritten": 1}
        assert _decode(fake.files[f"{root}/index/index.md"]) == (
            f"![p]({root}/index/logo.png)"
        )
        assert f"{root}/index/.image_mappings.json" not in fake.files

    async def test_split_doc_mapping_keys_resolved(self):
        # Keys produced for a split document are paths relative to the doc root
        # holding the mapping (possibly several levels deep).
        from openviking.parse.image_rewrite import rewrite_image_uris

        root = "viking://resources/res"
        fake = FakeVikingFS()
        fake.files = {
            f"{root}/big/标题/章一/部分_1.md": "![p](./assets/d.jpg)".encode(),
            f"{root}/big/标题/章一/d.jpg": b"img",
            f"{root}/big/.image_mappings.json": (
                '{"标题/章一/部分_1.md": {"./assets/d.jpg": "d.jpg"}}'.encode()
            ),
        }

        with self._patched(fake):
            stats = await rewrite_image_uris(root, lock_handle=None)

        assert stats == {"files_processed": 1, "references_rewritten": 1}
        assert _decode(fake.files[f"{root}/big/标题/章一/部分_1.md"]) == (
            f"![p]({root}/big/标题/章一/d.jpg)"
        )
        assert f"{root}/big/.image_mappings.json" not in fake.files

    async def test_root_mapping_still_works(self):
        # Single-file ingest leaves the mapping at the resource root; that layout
        # must keep working unchanged.
        from openviking.parse.image_rewrite import rewrite_image_uris

        root = "viking://resources/doc"
        fake = FakeVikingFS()
        fake.files = {
            f"{root}/doc.md": "![p](./assets/logo.png)".encode(),
            f"{root}/logo.png": b"img",
            f"{root}/.image_mappings.json": (
                '{"doc.md": {"./assets/logo.png": "logo.png"}}'.encode()
            ),
        }

        with self._patched(fake):
            stats = await rewrite_image_uris(root, lock_handle=None)

        assert stats == {"files_processed": 1, "references_rewritten": 1}
        assert _decode(fake.files[f"{root}/doc.md"]) == f"![p]({root}/logo.png)"
        assert f"{root}/.image_mappings.json" not in fake.files

    async def test_merge_temp_carries_sidecar_but_not_other_hidden_files(self):
        # _merge_temp must carry the declared .image_mappings.json sidecar, but
        # keep filtering every other hidden file a parser (or anything else)
        # may have left in its temp tree.
        src = "viking://temp/one"
        dest = "viking://temp/dir/docs"
        fake = FakeVikingFS()
        fake.files = {
            f"{src}/doc/doc.md": b"![p](./a.png)",
            f"{src}/doc/a.png": b"img",
            f"{src}/doc/.image_mappings.json": b'{"doc.md": {"./a.png": "a.png"}}',
            f"{src}/doc/.stray_hidden": b"must not be merged",
        }

        await DirectoryParser._merge_temp(fake, src, dest)

        assert f"{dest}/doc/.image_mappings.json" in fake.files, fake.files
        assert f"{dest}/doc/doc.md" in fake.files
        assert not any(u.endswith(".stray_hidden") for u in fake.files), fake.files

    async def test_sync_path_carries_nested_mappings_to_target(self):
        # Re-ingest of an existing resource goes through SemanticProcessor's
        # temp->target sync, which MOVES the visible files into the target and
        # skips hidden ones: afterwards the temp tree holds ONLY the
        # .image_mappings.json sidecars (the md files are gone). They must
        # still be carried over so the rewrite happens in the target tree.
        import openviking.storage.queuefs.semantic_processor as sp_mod
        from openviking.storage.queuefs.semantic_processor import SemanticProcessor

        root = "viking://temp/sync_src"
        target = "viking://resources/res"
        fake = FakeVikingFS()
        fake.files = {
            # temp tree after sync: visible files moved away, sidecar left behind
            f"{root}/index/.image_mappings.json": (
                '{"index.md": {"./assets/logo.png": "logo.png"}}'.encode()
            ),
            # target tree after sync: visible files only, no hidden sidecar
            f"{target}/index/index.md": "![p](./assets/logo.png)".encode(),
            f"{target}/index/logo.png": b"img",
        }

        processor = SemanticProcessor.__new__(SemanticProcessor)
        with patch.object(sp_mod, "get_viking_fs", return_value=fake), patch.object(
            __import__("openviking.parse.image_rewrite", fromlist=["x"]),
            "get_viking_fs",
            return_value=fake,
        ):
            await processor._rewrite_target_image_uris(root, target)

        assert _decode(fake.files[f"{target}/index/index.md"]) == (
            f"![p]({target}/index/logo.png)"
        )

    async def test_directory_ingest_images_become_viking_uris(self, tmp_path: Path):
        # End to end: directory ingest -> persist -> rewrite. Every md referencing
        # an ingestable image must end up with a viking:// URI.
        from openviking.parse.image_rewrite import rewrite_image_uris

        kb = tmp_path / "kb"
        (kb / "assets").mkdir(parents=True)
        _write_valid_png(kb / "assets" / "logo.png")
        (kb / "index.md").write_text("![logo](./assets/logo.png)", encoding="utf-8")
        (kb / "guide.md").write_text("![logo2](./assets/logo.png)", encoding="utf-8")

        fake = FakeVikingFS()
        with patch.object(BaseParser, "_get_viking_fs", return_value=fake):
            result = await DirectoryParser().parse(str(kb))

            # Simulate finalize_from_temp + persist_temp_tree: the single doc dir
            # under the temp root is mirrored onto the final resource root.
            temp = result.temp_dir_path
            entries = await fake.ls(temp)
            doc_dirs = [e for e in entries if e["isDir"]]
            assert len(doc_dirs) == 1, entries
            root = f"viking://resources/{doc_dirs[0]['name']}"
            src_prefix = doc_dirs[0]["uri"].rstrip("/") + "/"
            for u in list(fake.files):
                if u.startswith(src_prefix):
                    fake.files[f"{root}/{u[len(src_prefix):]}"] = fake.files[u]

            with self._patched(fake):
                stats = await rewrite_image_uris(root, lock_handle=None)

        assert stats["files_processed"] == 2, stats
        index_md = _decode(fake.files[f"{root}/index/index.md"])
        guide_md = _decode(fake.files[f"{root}/guide/guide.md"])
        assert f"![logo]({root}/index/logo.png)" in index_md, index_md
        assert f"![logo2]({root}/guide/logo.png)" in guide_md, guide_md
        assert not any(
            u.endswith(".image_mappings.json") and u.startswith(root)
            for u in fake.files
        ), fake.files
