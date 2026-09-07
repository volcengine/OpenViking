import os
import uuid


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

    def test_cp_rejects_existing_target_without_changing_either_file(self, api_client):
        suffix = uuid.uuid4().hex[:8]
        source = f"viking://resources/cp-conflict-source-{suffix}.md"
        target = f"viking://resources/cp-conflict-target-{suffix}.md"
        try:
            assert (
                api_client.fs_write(source, "source remains", mode="create", wait=True).status_code
                == 200
            )
            assert (
                api_client.fs_write(target, "target remains", mode="create", wait=True).status_code
                == 200
            )

            copied = api_client.fs_cp(source, target)
            assert copied.status_code == 409, copied.text
            assert "source remains" in api_client.fs_read(source).json().get("result", "")
            assert "target remains" in api_client.fs_read(target).json().get("result", "")
        finally:
            api_client.fs_rm(source)
            api_client.fs_rm(target)
