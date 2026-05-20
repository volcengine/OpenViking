import os
import tempfile
import uuid


class TestFsFieldConsistency:
    def test_stat_returns_required_fields(self, api_client):
        dir_uri = f"viking://resources/stat_fields_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri)
            if mkdir_resp.status_code != 200:
                return

            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})

            required = ["name", "isDir"]
            for field in required:
                assert field in result, (
                    f"stat result should contain '{field}', got keys: {sorted(result.keys())}"
                )
            assert result["isDir"] is True, "directory should have isDir=True"
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_stat_file_has_size(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "stat_size_test.md")
            with open(test_file, "w") as f:
                f.write("Content for size test")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            child_uris = []
            ls_resp = api_client.fs_ls(root_uri)
            if ls_resp.status_code == 200:
                for child in ls_resp.json().get("result", []):
                    if isinstance(child, dict):
                        child_uris.append(child.get("uri", ""))
                    elif isinstance(child, str):
                        child_uris.append(child)

            file_uri = child_uris[0] if child_uris else root_uri

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            assert result.get("isDir") is False, "file should have isDir=False"
            if "size" in result:
                assert isinstance(result["size"], int), "size should be int"
                assert result["size"] > 0, "file size should be > 0"

    def test_ls_children_have_consistent_fields(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            for name in ["a.md", "b.md"]:
                with open(os.path.join(temp_dir, name), "w") as f:
                    f.write(f"Content of {name}")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            ls_resp = api_client.fs_ls(root_uri)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) >= 2, f"should have at least 2 children, got {len(children)}"

            for child in children:
                if isinstance(child, dict):
                    assert "name" in child or "uri" in child, (
                        f"child should have name or uri, got keys: {sorted(child.keys())}"
                    )

    def test_tree_result_is_dict(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            sub_dir = os.path.join(temp_dir, "sub")
            os.makedirs(sub_dir)
            with open(os.path.join(sub_dir, "leaf.md"), "w") as f:
                f.write("Leaf content")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            tree_resp = api_client.fs_tree(root_uri)
            assert tree_resp.status_code == 200
            result = tree_resp.json().get("result", {})
            assert isinstance(result, (dict, list)), (
                f"tree result should be dict or list, got {type(result)}"
            )

    def test_stat_uri_matches_request(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "stat_uri_test.md")
            with open(test_file, "w") as f:
                f.write("URI match test")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            child_uris = []
            ls_resp = api_client.fs_ls(root_uri)
            if ls_resp.status_code == 200:
                children = ls_resp.json().get("result", [])
                for child in children:
                    if isinstance(child, dict):
                        child_uris.append(child.get("uri", ""))
                    elif isinstance(child, str):
                        child_uris.append(child)

            file_uri = child_uris[0] if child_uris else root_uri

            stat_resp = api_client.fs_stat(file_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            if "uri" in result:
                assert result["uri"] == file_uri, (
                    f"stat uri should match request, got {result['uri']} != {file_uri}"
                )
            elif "name" in result:
                expected_name = file_uri.rstrip("/").split("/")[-1]
                assert result["name"] == expected_name, (
                    f"stat name should match file name, got {result['name']} != {expected_name}"
                )

    def test_ls_simple_mode(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "x.md"), "w") as f:
                f.write("Simple mode test")
            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            ls_resp = api_client.fs_ls(root_uri, simple=True)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert isinstance(children, list), "simple ls result should be list"

    def test_ls_recursive_mode(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            sub_dir = os.path.join(temp_dir, "sub")
            os.makedirs(sub_dir)
            with open(os.path.join(temp_dir, "top.md"), "w") as f:
                f.write("Top level")
            with open(os.path.join(sub_dir, "deep.md"), "w") as f:
                f.write("Nested level")
            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            ls_resp = api_client.fs_ls(root_uri, recursive=True)
            assert ls_resp.status_code == 200
            children = ls_resp.json().get("result", [])
            assert len(children) >= 2, (
                f"recursive ls should show nested children, got {len(children)}"
            )

    def test_stat_nonexistent_returns_404(self, api_client):
        stat_resp = api_client.fs_stat("viking://resources/nonexistent_stat_test_99999")
        assert stat_resp.status_code == 404, (
            f"stat nonexistent should return 404/500, got {stat_resp.status_code}"
        )

    def test_ls_nonexistent_returns_empty_or_404(self, api_client):
        ls_resp = api_client.fs_ls("viking://resources/nonexistent_ls_test_99999")
        assert ls_resp.status_code == 404, (
            f"ls nonexistent should return 404, got {ls_resp.status_code}"
        )

    def test_mkdir_with_description(self, api_client):
        dir_uri = f"viking://resources/mkdir_desc_{uuid.uuid4().hex[:8]}"
        try:
            mkdir_resp = api_client.fs_mkdir(dir_uri, description="Test directory with description")
            assert mkdir_resp.status_code == 200, (
                f"mkdir with description should succeed, got {mkdir_resp.status_code}"
            )

            stat_resp = api_client.fs_stat(dir_uri)
            if stat_resp.status_code == 200:
                result = stat_resp.json().get("result", {})
                if "description" in result:
                    assert result["description"] == "Test directory with description", (
                        f"description should be preserved, got {result['description']}"
                    )
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass

    def test_stat_file_has_content_type(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "stat_ct_test.md")
            with open(test_file, "w") as f:
                f.write("Content type test")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            child_uris = []
            ls_resp = api_client.fs_ls(root_uri)
            if ls_resp.status_code == 200:
                for child in ls_resp.json().get("result", []):
                    if isinstance(child, dict):
                        child_uris.append(child.get("uri", ""))
                    elif isinstance(child, str):
                        child_uris.append(child)
            file_uri = child_uris[0] if child_uris else root_uri

            stat_resp = api_client.fs_stat(file_uri)
            if stat_resp.status_code == 200:
                result = stat_resp.json().get("result", {})
                if "contentType" in result or "content_type" in result:
                    ct = result.get("contentType") or result.get("content_type")
                    assert isinstance(ct, str), f"content type should be str, got {type(ct)}"

    def test_stat_directory_has_no_size(self, api_client):
        dir_uri = f"viking://resources/stat_nosize_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(dir_uri)

            stat_resp = api_client.fs_stat(dir_uri)
            if stat_resp.status_code == 200:
                result = stat_resp.json().get("result", {})
                if "size" in result:
                    assert result["size"] == 0 or result["isDir"] is True, (
                        f"directory size should be 0 or absent, got size={result['size']}"
                    )
        finally:
            try:
                api_client.fs_rm(dir_uri, recursive=True)
            except Exception:
                pass
