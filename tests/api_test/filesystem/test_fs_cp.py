import os
import uuid

import pytest


class TestFsCp:
    def test_cp_file_preserves_source_and_content(self, api_client):
        suffix = uuid.uuid4().hex[:8]
        source = f"viking://resources/cp-source-{suffix}.md"
        target = f"viking://resources/cp-target-{suffix}.md"
        content = f"copy payload {suffix}"
        try:
            write = api_client.fs_write(source, content, mode="create", wait=True)
            assert write.status_code == 200

            copied = api_client.fs_cp(source, target)
            assert copied.status_code == 200, copied.text
            result = copied.json().get("result", {})
            assert result.get("from") == source
            assert result.get("to") == target
            assert result.get("recursive") is False

            for uri in (source, target):
                stat = api_client.fs_stat(uri)
                assert stat.status_code == 200
                read = api_client.fs_read(uri)
                assert read.status_code == 200
                assert content in read.json().get("result", "")

            if os.getenv("HAS_SECRETS", "true").lower() == "true":
                found = api_client.find(query=suffix, target_uri=target, limit=5)
                assert found.status_code == 200
                resources = found.json().get("result", {}).get("resources", [])
                assert any(item.get("uri") == target for item in resources)

            assert api_client.fs_rm(source).status_code == 200
            assert api_client.fs_read(target).status_code == 200
        finally:
            api_client.fs_rm(source)
            api_client.fs_rm(target)

    def test_cp_directory_requires_recursive_and_copies_tree(self, api_client):
        suffix = uuid.uuid4().hex[:8]
        source = f"viking://resources/cp-dir-source-{suffix}"
        target = f"viking://resources/cp-dir-target-{suffix}"
        child = f"{source}/nested/child.md"
        try:
            assert api_client.fs_mkdir(source).status_code == 200
            assert api_client.fs_mkdir(f"{source}/nested").status_code == 200
            assert api_client.fs_mkdir(f"{source}/empty").status_code == 200
            assert (
                api_client.fs_write(
                    child, "recursive copy child", mode="create", wait=True
                ).status_code
                == 200
            )

            without_recursive = api_client.fs_cp(source, target)
            assert without_recursive.status_code == 412, without_recursive.text

            copied = api_client.fs_cp(source, target, recursive=True)
            assert copied.status_code == 200, copied.text
            assert api_client.fs_read(f"{target}/nested/child.md").status_code == 200
            tree = api_client.fs_tree(target)
            assert tree.status_code == 200
            tree_text = tree.text
            assert "nested/child.md" in tree_text
            assert "empty" in tree_text
        finally:
            api_client.fs_rm(source, recursive=True)
            api_client.fs_rm(target, recursive=True)

    def test_cp_overwrites_existing_target_and_preserves_source(self, api_client):
        suffix = uuid.uuid4().hex[:8]
        source = f"viking://resources/cp-overwrite-source-{suffix}.md"
        target = f"viking://resources/cp-overwrite-target-{suffix}.md"
        source_content = f"new payload {suffix}"
        target_content = f"old target content must be completely replaced {suffix}"
        try:
            assert (
                api_client.fs_write(source, source_content, mode="create", wait=True).status_code
                == 200
            )
            assert (
                api_client.fs_write(target, target_content, mode="create", wait=True).status_code
                == 200
            )

            copied = api_client.fs_cp(source, target)
            assert copied.status_code == 200, copied.text
            result = copied.json().get("result", {})
            assert result.get("from") == source
            assert result.get("to") == target
            assert result.get("phase") == "completed"
            assert result.get("recursive") is False
            for uri in (source, target):
                read = api_client.fs_read(uri)
                assert read.status_code == 200, read.text
                assert read.json().get("result") == source_content
        finally:
            api_client.fs_rm(source)
            api_client.fs_rm(target)

    @pytest.mark.parametrize("source_is_dir", [False, True], ids=["file-to-dir", "dir-to-file"])
    def test_cp_rejects_file_directory_type_conflicts(self, api_client, source_is_dir):
        suffix = uuid.uuid4().hex[:8]
        file_uri = f"viking://resources/cp-type-file-{suffix}.md"
        directory_uri = f"viking://resources/cp-type-dir-{suffix}"
        child_uri = f"{directory_uri}/child.md"
        file_content = f"file remains {suffix}"
        child_content = f"directory child remains {suffix}"
        try:
            assert api_client.fs_mkdir(directory_uri).status_code == 200
            # mkdir queues a parent refresh; finish it before preparing files so
            # this test exercises type validation, not a transient refresh lock.
            settled = api_client.system_wait(timeout=30)
            assert settled.status_code == 200, settled.text
            for uri, content in ((file_uri, file_content), (child_uri, child_content)):
                write = api_client.fs_write(uri, content, mode="create", wait=True)
                assert write.status_code == 200, write.text

            source, target = (
                (directory_uri, file_uri) if source_is_dir else (file_uri, directory_uri)
            )
            copied = api_client.fs_cp(source, target, recursive=source_is_dir)
            assert copied.status_code == 400, copied.text
            assert copied.json().get("error", {}).get("code") == "INVALID_ARGUMENT"
            for uri, content in ((file_uri, file_content), (child_uri, child_content)):
                read = api_client.fs_read(uri)
                assert read.status_code == 200, read.text
                assert read.json().get("result") == content
        finally:
            api_client.fs_rm(file_uri)
            api_client.fs_rm(directory_uri, recursive=True)
