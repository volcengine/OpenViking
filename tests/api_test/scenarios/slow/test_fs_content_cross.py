import os
import tempfile
import uuid


class TestFsContentCrossValidation:
    def test_write_then_stat_size_nonzero(self, api_client):
        file_uri = f"viking://resources/cross_size_{uuid.uuid4().hex[:8]}.md"
        content = "A" * 500
        try:
            write_resp = api_client.fs_write(file_uri, content, mode="create", wait=True)
            if write_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 200
            stat_result = stat_resp.json().get("result", {})
            assert stat_result.get("size", 0) > 0, (
                f"file size should be > 0 after writing {len(content)} chars"
            )
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass

    def test_mv_file_then_read_from_new_uri(self, api_client):
        src_uri = f"viking://resources/cross_mv_src_{uuid.uuid4().hex[:8]}.md"
        dst_uri = f"viking://resources/cross_mv_dst_{uuid.uuid4().hex[:8]}.md"
        try:
            write_resp = api_client.fs_write(
                src_uri, "Content before mv operation", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            mv_resp = api_client.fs_mv(src_uri, dst_uri)
            if mv_resp.status_code != 200:
                return

            read_resp = api_client.fs_read(dst_uri)
            assert read_resp.status_code == 200, (
                f"should be able to read from new URI after mv, got {read_resp.status_code}"
            )
            content = read_resp.json().get("result", "")
            assert "before mv" in content, (
                f"content should be preserved after mv, got: {content[:200]}"
            )

            read_old = api_client.fs_read(src_uri)
            assert read_old.status_code == 404, (
                f"old URI should not be readable after mv, got {read_old.status_code}"
            )
        finally:
            try:
                api_client.fs_rm(src_uri)
                api_client.fs_rm(dst_uri)
            except Exception:
                pass

    def test_write_then_overview_readable(self, api_client):
        dir_uri = f"viking://resources/cross_ov_{uuid.uuid4().hex[:8]}"
        file_uri = f"{dir_uri}/doc.md"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            write_resp = api_client.fs_write(
                file_uri,
                "This document covers distributed systems design patterns including consensus algorithms.",
                mode="create",
                wait=True,
            )
            if write_resp.status_code != 200:
                return

            api_client.wait_processed()

            overview_resp = api_client.get_overview(dir_uri)
            assert overview_resp.status_code in (200, 412), (
                f"overview should be readable or still processing, got {overview_resp.status_code}"
            )
            if overview_resp.status_code == 200:
                overview = overview_resp.json().get("result", "")
                assert isinstance(overview, str), "overview should be string"
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_rm_directory_then_stat_returns_404(self, api_client):
        dir_uri = f"viking://resources/cross_rm_dir_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            stat_before = api_client.fs_stat(dir_uri)
            assert stat_before.status_code == 200

            rm_resp = api_client.fs_rm(dir_uri, recursive=True)
            assert rm_resp.status_code == 200

            stat_after = api_client.fs_stat(dir_uri)
            assert stat_after.status_code == 404, (
                f"directory should not exist after rm, got {stat_after.status_code}"
            )
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_resource_add_then_content_read_matches(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"cross_read_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            original_content = f"# {unique_kw}\n\nThis is unique content for read verification."
            with open(test_file, "w") as f:
                f.write(original_content)

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            api_client.wait_processed()

            ls_resp = api_client.fs_ls(root_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, (
                f"resource directory should have children, root_uri={root_uri}"
            )

            for child in children:
                if isinstance(child, dict):
                    child_uri = child.get("uri", "")
                    child_name = child.get("name", "")
                    if child_name.endswith(".md") and not child.get("isDir", False):
                        read_resp = api_client.fs_read(child_uri)
                        if read_resp.status_code == 200:
                            content = read_resp.json().get("result", "")
                            assert unique_kw in content, (
                                f"read content should contain original keyword {unique_kw}"
                            )
                            break

    def test_mv_directory_then_children_readable(self, api_client):
        src_dir = f"viking://resources/cross_mvdir_s_{uuid.uuid4().hex[:8]}"
        dst_dir = f"viking://resources/cross_mvdir_d_{uuid.uuid4().hex[:8]}"
        file_uri = f"{src_dir}/inner.md"
        try:
            mkdir_resp = api_client.fs_mkdir(src_dir)
            if mkdir_resp.status_code != 200:
                return

            write_resp = api_client.fs_write(
                file_uri, "Inner file content before mv", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            mv_resp = api_client.fs_mv(src_dir, dst_dir)
            if mv_resp.status_code != 200:
                return

            ls_resp = api_client.fs_ls(dst_dir)
            assert ls_resp.status_code == 200, (
                f"should be able to ls moved directory, got {ls_resp.status_code}"
            )
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, "moved directory should still contain children"
        finally:
            try:
                api_client.fs_rm(src_dir, recursive=True)
                api_client.fs_rm(dst_dir, recursive=True)
            except Exception:
                pass

    def test_reindex_then_grep_still_finds(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"cross_grep_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent for grep after reindex.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_before = api_client.grep(root_uri, unique_kw)
            assert grep_before.status_code == 200

            reindex_resp = api_client.content_reindex(root_uri, regenerate=False, wait=True)
            if reindex_resp.status_code != 200:
                return

            grep_after = api_client.grep(root_uri, unique_kw)
            assert grep_after.status_code == 200
            matches_after = grep_after.json().get("result", {}).get("matches", [])
            assert len(matches_after) > 0, (
                f"grep should still find content after reindex, query={unique_kw}"
            )

    def test_multiple_writes_same_uri_last_wins(self, api_client):
        file_uri = f"viking://resources/cross_multi_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(file_uri, "First write content", mode="create", wait=True)
            api_client.fs_write(file_uri, "Second write replaces first", mode="replace", wait=True)
            api_client.fs_write(file_uri, "\nThird write appends", mode="append", wait=True)

            read_resp = api_client.fs_read(file_uri)
            if read_resp.status_code == 200:
                content = read_resp.json().get("result", "")
                assert "Second write replaces first" in content, (
                    f"replace content should be present, got: {content[:200]}"
                )
                assert "Third write appends" in content, (
                    f"append content should be present, got: {content[:200]}"
                )
                assert "First write content" not in content, (
                    f"original content should be gone after replace, got: {content[:200]}"
                )
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass
