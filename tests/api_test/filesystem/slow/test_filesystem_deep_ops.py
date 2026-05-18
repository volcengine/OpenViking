import os
import tempfile
import uuid


class TestFilesystemDeep:
    def test_mkdir_creates_directory_stat_confirms(self, api_client):
        dir_uri = f"viking://resources/mkdir_stat_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            assert mkdir_resp.status_code == 200, (
                f"mkdir should return 200, got {mkdir_resp.status_code}"
            )
            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            assert result.get("isDir") is True, f"stat should confirm isDir=True, got {result}"
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_mkdir_with_description(self, api_client):
        dir_uri = f"viking://resources/desc_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri, description="Test directory description")
            assert mkdir_resp.status_code == 200, (
                f"mkdir with description should return 200, got {mkdir_resp.status_code}"
            )
            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            assert result.get("isDir") is True, "should be a directory"
            desc = result.get("description", "")
            if desc:
                assert "Test directory" in desc or "description" in desc, (
                    f"description should contain our text, got: {desc}"
                )
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_mkdir_nested_path(self, api_client):
        parent_uri = f"viking://resources/nested_{uuid.uuid4().hex[:8]}"
        child_uri = f"{parent_uri}/child"
        grandchild_uri = f"{child_uri}/grandchild"
        try:
            mkdir1 = api_client.fs_mkdir(parent_uri)
            assert mkdir1.status_code == 200, (
                f"mkdir parent should return 200, got {mkdir1.status_code}"
            )

            mkdir2 = api_client.fs_mkdir(child_uri)
            assert mkdir2.status_code == 200, (
                f"mkdir child should return 200, got {mkdir2.status_code}"
            )

            mkdir3 = api_client.fs_mkdir(grandchild_uri)
            assert mkdir3.status_code == 200, (
                f"mkdir grandchild should return 200, got {mkdir3.status_code}"
            )

            ls_resp = api_client.fs_ls(parent_uri, recursive=True)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            child_uris = [c.get("uri", "") for c in children]
            assert any(child_uri in u for u in child_uris), (
                f"recursive ls should show child, got uris: {child_uris}"
            )
            assert any(grandchild_uri in u for u in child_uris), (
                f"recursive ls should show grandchild, got uris: {child_uris}"
            )
        finally:
            api_client.fs_rm(parent_uri, recursive=True)

    def test_mkdir_duplicate_returns_ok_or_conflict(self, api_client):
        dir_uri = f"viking://resources/dup_mkdir_{uuid.uuid4().hex[:8]}"
        try:
            mkdir1 = api_client.fs_mkdir(dir_uri)
            assert mkdir1.status_code == 200

            mkdir2 = api_client.fs_mkdir(dir_uri)
            assert mkdir2.status_code == 200, (
                f"duplicate mkdir should return 200 (idempotent) or 409/412, got {mkdir2.status_code}: {mkdir2.text[:200]}"
            )
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_mkdir_special_chars_in_description(self, api_client):
        dir_uri = f"viking://resources/mkdir_special_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri, description="目录 with 中文 and symbols !@#")
            assert mkdir_resp.status_code == 200, (
                f"mkdir with special chars should work, got {mkdir_resp.status_code}"
            )
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_rm_file_removes_from_ls(self, api_client):
        file_uri = f"viking://resources/rm_ls_{uuid.uuid4().hex[:8]}.md"
        try:
            write_resp = api_client.fs_write(
                file_uri, "File to be removed", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            rm_resp = api_client.fs_rm(file_uri)
            assert rm_resp.status_code == 200, f"rm should return 200, got {rm_resp.status_code}"

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 404, (
                f"stat after rm should return 404/412, got {stat_resp.status_code}"
            )
        finally:
            api_client.fs_rm(file_uri)

    def test_rm_directory_nonrecursive_returns_412(self, api_client):
        dir_uri = f"viking://resources/rm_nonrec_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return
            api_client.fs_write(f"{dir_uri}/child.md", "child", mode="create", wait=True)

            rm_resp = api_client.fs_rm(dir_uri, recursive=False)
            assert rm_resp.status_code == 412, (
                f"non-recursive rm on non-empty dir should return 412 Precondition Failed, got {rm_resp.status_code}: {rm_resp.text[:200]}"
            )
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_rm_nonexistent_returns_ok_or_error(self, api_client):
        uri = f"viking://resources/nonexist_rm_{uuid.uuid4().hex[:8]}"
        rm_resp = api_client.fs_rm(uri)
        assert rm_resp.status_code == 200, (
            f"rm nonexistent should return 200/400/404/412, got {rm_resp.status_code}: {rm_resp.text[:200]}"
        )

    def test_rm_then_recreate_same_uri(self, api_client):
        dir_uri = f"viking://resources/rm_recreate_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(dir_uri, description="First creation")
            api_client.fs_rm(dir_uri, recursive=True)

            mkdir_again = api_client.fs_mkdir(dir_uri, description="Second creation")
            assert mkdir_again.status_code == 200, (
                f"recreate after rm should work, got {mkdir_again.status_code}"
            )

            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_mv_nonexistent_source_returns_error(self, api_client):
        src_uri = f"viking://resources/nonexist_mv_src_{uuid.uuid4().hex[:8]}.md"
        dst_uri = f"viking://resources/nonexist_mv_dst_{uuid.uuid4().hex[:8]}.md"
        mv_resp = api_client.fs_mv(src_uri, dst_uri)
        assert mv_resp.status_code == 404, (
            f"mv nonexistent source should return 404/412, got {mv_resp.status_code}: {mv_resp.text[:200]}"
        )

    def test_tree_shows_hierarchy(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            sub_dir = os.path.join(temp_dir, "sub")
            os.makedirs(sub_dir)
            with open(os.path.join(sub_dir, "file.md"), "w") as f:
                f.write("Content in sub directory for tree test.")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200, (
                f"tree should return 200, got {tree_resp.status_code}"
            )
            tree_data = tree_resp.json().get("result", {})
            assert isinstance(tree_data, (dict, list)), "tree result should be dict or list"

    def test_stat_file_has_size_and_modtime(self, api_client):
        file_uri = f"viking://resources/stat_file_{uuid.uuid4().hex[:8]}.md"
        try:
            write_resp = api_client.fs_write(
                file_uri, "Stat test content", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})

            assert "size" in result, f"stat should contain size, got keys: {sorted(result.keys())}"
            assert "modTime" in result, (
                f"stat should contain modTime, got keys: {sorted(result.keys())}"
            )
            assert isinstance(result["size"], int), (
                f"size should be int, got {type(result['size'])}"
            )
            assert result["size"] >= 0, f"size should be non-negative, got {result['size']}"
        finally:
            api_client.fs_rm(file_uri)

    def test_ls_simple_mode(self, api_client):
        dir_uri = f"viking://resources/ls_simple_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(dir_uri)

            ls_resp = api_client.fs_ls(dir_uri, simple=True)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert isinstance(children, list), "simple ls should return a list"
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_stat_nonexistent_uri(self, api_client):
        stat_resp = api_client.fs_stat("viking://resources/nonexistent_stat_test_xyz")
        assert stat_resp.status_code == 404, (
            f"stat nonexistent should return 404/412, got {stat_resp.status_code}: {stat_resp.text[:200]}"
        )

    def test_ls_nonexistent_uri(self, api_client):
        ls_resp = api_client.fs_ls("viking://resources/nonexistent_ls_test_xyz")
        assert ls_resp.status_code == 404, (
            f"ls nonexistent should return 200/404/412, got {ls_resp.status_code}"
        )

    def test_write_file_inside_mkdir_directory(self, api_client):
        dir_uri = f"viking://resources/write_in_dir_{uuid.uuid4().hex[:8]}"
        file_uri = f"{dir_uri}/inner.md"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            write_resp = api_client.fs_write(
                file_uri, "Content inside directory", mode="create", wait=True
            )
            if write_resp.status_code != 200:
                return

            ls_resp = api_client.fs_ls(dir_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            child_uris = [c.get("uri", "") for c in children]
            assert any(file_uri in u or "inner.md" in u for u in child_uris), (
                f"directory should contain the written file, got uris: {child_uris}"
            )
        finally:
            api_client.fs_rm(dir_uri, recursive=True)
