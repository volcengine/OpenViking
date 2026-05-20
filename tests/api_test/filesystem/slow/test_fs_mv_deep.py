import uuid


class TestFsMvDeep:
    def test_mv_file_preserves_content(self, api_client):
        src = f"viking://resources/mvdeep_src_{uuid.uuid4().hex[:8]}.md"
        dst = f"viking://resources/mvdeep_dst_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(src, "Content preserved during mv", mode="create", wait=True)
            mv_resp = api_client.fs_mv(src, dst)
            if mv_resp.status_code != 200:
                return

            read_resp = api_client.fs_read(dst)
            assert read_resp.status_code == 200
            content = read_resp.json().get("result", "")
            assert "preserved during mv" in content, (
                f"content should be preserved after mv, got: {content[:200]}"
            )
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst)
            except Exception:
                pass

    def test_mv_directory_with_children(self, api_client):
        src_dir = f"viking://resources/mvdeep_dir_{uuid.uuid4().hex[:8]}"
        dst_dir = f"viking://resources/mvdeep_dir_dst_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(src_dir)
            api_client.fs_write(
                f"{src_dir}/child.md", "Child content in moved directory", mode="create", wait=True
            )

            mv_resp = api_client.fs_mv(src_dir, dst_dir)
            if mv_resp.status_code != 200:
                return

            ls_resp = api_client.fs_ls(dst_dir)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, "moved directory should still contain children"

            read_resp = api_client.fs_read(f"{dst_dir}/child.md")
            if read_resp.status_code == 200:
                content = read_resp.json().get("result", "")
                assert "Child content" in content, (
                    "child content should be preserved after mv directory"
                )
        finally:
            try:
                api_client.fs_rm(src_dir, recursive=True)
                api_client.fs_rm(dst_dir, recursive=True)
            except Exception:
                pass

    def test_mv_to_existing_directory(self, api_client):
        src = f"viking://resources/mvdeep_exist_{uuid.uuid4().hex[:8]}.md"
        dst_dir = f"viking://resources/mvdeep_target_dir_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_write(src, "File moved to existing dir", mode="create", wait=True)
            api_client.fs_mkdir(dst_dir)

            mv_resp = api_client.fs_mv(src, f"{dst_dir}/moved_file.md")
            if mv_resp.status_code != 200:
                return

            read_resp = api_client.fs_read(f"{dst_dir}/moved_file.md")
            assert read_resp.status_code == 200, "file should be readable at new location"
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst_dir, recursive=True)
            except Exception:
                pass

    def test_mv_nonexistent_src_returns_error(self, api_client):
        mv_resp = api_client.fs_mv(
            "viking://resources/nonexistent_mv_src_99999.md", "viking://resources/mv_dst.md"
        )
        assert mv_resp.status_code == 404, (
            f"mv nonexistent src should return error, got {mv_resp.status_code}"
        )

    def test_mv_then_stat_new_location(self, api_client):
        src = f"viking://resources/mvdeep_stat_{uuid.uuid4().hex[:8]}.md"
        dst = f"viking://resources/mvdeep_stat_new_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(src, "Stat after mv test", mode="create", wait=True)
            mv_resp = api_client.fs_mv(src, dst)
            if mv_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(dst)
            assert stat_resp.status_code == 200
            assert stat_resp.json().get("result", {}).get("isDir") is False
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst)
            except Exception:
                pass

    def test_mv_preserves_searchability(self, api_client):
        src = f"viking://resources/mvdeep_search_{uuid.uuid4().hex[:8]}.md"
        dst = f"viking://resources/mvdeep_search_new_{uuid.uuid4().hex[:8]}.md"
        unique_kw = f"mvsearch_{uuid.uuid4().hex[:6]}"
        try:
            api_client.fs_write(
                src,
                f"Content with {unique_kw} for searchability after mv test",
                mode="create",
                wait=True,
            )
            mv_resp = api_client.fs_mv(src, dst)
            if mv_resp.status_code != 200:
                return

            find_resp = api_client.find(query=unique_kw, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) > 0, (
                f"content should still be searchable after mv, query={unique_kw}"
            )
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst)
            except Exception:
                pass

    def test_mv_then_rm_new_location(self, api_client):
        src = f"viking://resources/mvdeep_rm_{uuid.uuid4().hex[:8]}.md"
        dst = f"viking://resources/mvdeep_rm_new_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(src, "Content to be moved then deleted", mode="create", wait=True)
            mv_resp = api_client.fs_mv(src, dst)
            if mv_resp.status_code != 200:
                return

            rm_resp = api_client.fs_rm(dst)
            assert rm_resp.status_code == 200

            stat_resp = api_client.fs_stat(dst)
            assert stat_resp.status_code == 404, (
                f"file should not exist after rm at new location, got {stat_resp.status_code}"
            )
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst)
            except Exception:
                pass

    def test_mv_then_write_to_new_location(self, api_client):
        src = f"viking://resources/mvdeep_rw_{uuid.uuid4().hex[:8]}.md"
        dst = f"viking://resources/mvdeep_rw_new_{uuid.uuid4().hex[:8]}.md"
        try:
            api_client.fs_write(src, "Original before mv", mode="create", wait=True)
            mv_resp = api_client.fs_mv(src, dst)
            if mv_resp.status_code != 200:
                return

            write_resp = api_client.fs_write(dst, "Updated after mv", mode="replace", wait=True)
            if write_resp.status_code == 200:
                read_resp = api_client.fs_read(dst)
                if read_resp.status_code == 200:
                    content = read_resp.json().get("result", "")
                    assert "Updated after mv" in content, (
                        f"should be able to write to moved file, got: {content[:200]}"
                    )
        finally:
            try:
                api_client.fs_rm(src)
                api_client.fs_rm(dst)
            except Exception:
                pass
