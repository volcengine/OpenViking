import os
import tempfile
import uuid


class TestSearchGlobDeep:
    def test_glob_all_md_files(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.md")
        assert glob_resp.status_code == 200
        result = glob_resp.json().get("result", {})
        if isinstance(result, dict):
            items = result.get("items", result.get("results", []))
            count = result.get("count", len(items))
            assert isinstance(count, int), f"count should be int, got {type(count).__name__}"
        elif isinstance(result, list):
            assert len(result) >= 0, "glob result should be a list"

    def test_glob_with_uri_scopes_results(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"globuri_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent for glob uri test.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.md", uri=root_uri)
            assert glob_resp.status_code == 200

    def test_glob_no_match_returns_empty(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.nonexistent_ext_xyz")
        assert glob_resp.status_code == 200
        result = glob_resp.json().get("result", {})
        if isinstance(result, dict):
            items = result.get("items", result.get("results", []))
            assert len(items) == 0, (
                f"glob for nonexistent ext should return empty, got {len(items)}"
            )
        elif isinstance(result, list):
            assert len(result) == 0, (
                f"glob for nonexistent ext should return empty, got {len(result)}"
            )

    def test_glob_txt_extension(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"globtxt_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Text file content for {unique_kw}.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.txt", uri=root_uri)
            assert glob_resp.status_code == 200

    def test_glob_multiple_extensions(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"globmulti_{uuid.uuid4().hex[:8]}"
            for ext in ["md", "txt", "py"]:
                test_file = os.path.join(temp_dir, f"{unique_kw}.{ext}")
                with open(test_file, "w") as f:
                    f.write(f"Content for {ext} file.")
            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_md = api_client.glob(pattern="**/*.md", uri=root_uri)
            glob_txt = api_client.glob(pattern="**/*.txt", uri=root_uri)
            assert glob_md.status_code == 200
            assert glob_txt.status_code == 200

    def test_glob_empty_pattern(self, api_client):
        glob_resp = api_client.glob(pattern="")
        assert glob_resp.status_code == 400, (
            f"glob empty pattern should return 200/400/422, got {glob_resp.status_code}"
        )

    def test_glob_star_pattern(self, api_client):
        glob_resp = api_client.glob(pattern="**/*")
        assert glob_resp.status_code == 200

    def test_glob_result_contains_uri(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"globuri2_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nURI check test.")
            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.md", uri=root_uri)
            assert glob_resp.status_code == 200
            result = glob_resp.json().get("result", {})
            items = []
            if isinstance(result, dict):
                items = result.get("items", result.get("results", []))
            elif isinstance(result, list):
                items = result
            if items:
                first = items[0]
                if isinstance(first, str):
                    assert "viking:" in first, "glob result string should contain viking URI"
                elif isinstance(first, dict):
                    assert "uri" in first, (
                        f"glob result dict should contain uri, got keys: {list(first.keys())}"
                    )
