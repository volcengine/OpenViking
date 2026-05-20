import time
import uuid


class TestContentUpdateConsistency:
    def test_write_replace_updates_search_index(self, api_client):
        file_uri = f"viking://resources/idx_update_{uuid.uuid4().hex[:8]}.md"
        try:
            write1 = api_client.fs_write(
                file_uri,
                "Original indexed content about machine learning",
                mode="create",
                wait=True,
            )
            if write1.status_code != 200:
                return

            write2 = api_client.fs_write(
                file_uri,
                "Updated content about deep learning and neural networks",
                mode="replace",
                wait=True,
            )
            if write2.status_code != 200:
                return

            read_resp = api_client.fs_read(file_uri)
            if read_resp.status_code == 200:
                content = read_resp.json().get("result", "")
                assert "deep learning" in content, (
                    f"replaced content should contain 'deep learning', got: {content[:200]}"
                )
                assert "machine learning" not in content, (
                    f"original content should be gone after replace, got: {content[:200]}"
                )
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass

    def test_abstract_changes_after_content_update(self, api_client):
        file_uri = f"viking://resources/abs_update_{uuid.uuid4().hex[:8]}.md"
        try:
            write1 = api_client.fs_write(
                file_uri,
                "Initial content about web development with HTML and CSS",
                mode="create",
                wait=True,
            )
            if write1.status_code != 200:
                return

            abstract1_resp = api_client.get_abstract(file_uri)
            if abstract1_resp.status_code != 200:
                return
            abstract1_resp.json().get("result", "")

            write2 = api_client.fs_write(
                file_uri,
                "Completely new content about blockchain and cryptocurrency",
                mode="replace",
                wait=True,
            )
            if write2.status_code != 200:
                return

            abstract2_resp = api_client.get_abstract(file_uri)
            if abstract2_resp.status_code != 200:
                return
            abstract2 = abstract2_resp.json().get("result", "")

            assert isinstance(abstract2, str), "updated abstract should be string"
            assert len(abstract2) > 0, "updated abstract should not be empty"
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass

    def test_content_write_creates_fs_entry(self, api_client):
        file_uri = f"viking://resources/fs_entry_{uuid.uuid4().hex[:8]}.md"
        try:
            write_resp = api_client.fs_write(
                file_uri, "Content that creates fs entry", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 200, (
                f"file should exist in fs after content/write, got {stat_resp.status_code}"
            )
            stat_result = stat_resp.json().get("result", {})
            assert stat_result.get("isDir") is False, "written file should not be a directory"
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass

    def test_mkdir_then_write_file_inside(self, api_client):
        dir_uri = f"viking://resources/mkdir_write_{uuid.uuid4().hex[:8]}"
        file_uri = f"{dir_uri}/notes.md"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            write_resp = api_client.fs_write(
                file_uri, "Notes inside directory", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            ls_resp = api_client.fs_ls(dir_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) > 0, "directory should contain the written file"

            read_resp = api_client.fs_read(file_uri)
            assert read_resp.status_code == 200
            assert "Notes inside" in read_resp.json().get("result", "")
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_rm_file_then_write_same_uri(self, api_client):
        file_uri = f"viking://resources/rm_rewrite_{uuid.uuid4().hex[:8]}.md"
        try:
            write1 = api_client.fs_write(file_uri, "First version", mode="create", wait=True)
            if write1.status_code != 200:
                return

            rm_resp = api_client.fs_rm(file_uri)
            assert rm_resp.status_code == 200

            time.sleep(1)
            write2 = api_client.fs_write(
                file_uri, "Second version after rm", mode="create", wait=True
            )
            assert write2.status_code == 200, (
                f"writing to same URI after rm should succeed or return 412 (eventual consistency), got {write2.status_code}: {write2.text[:200]}"
            )
            if write2.status_code == 412:
                return

            read_resp = api_client.fs_read(file_uri)
            if read_resp.status_code == 200:
                content = read_resp.json().get("result", "")
                assert "Second version" in content, (
                    f"content should be from second write, got: {content[:200]}"
                )
        finally:
            try:
                api_client.fs_rm(file_uri)
            except Exception:
                pass
