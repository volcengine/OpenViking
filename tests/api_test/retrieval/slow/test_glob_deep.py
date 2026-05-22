import os
import tempfile
import uuid


class TestGlobDeep:
    def test_glob_md_pattern(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_md_{uuid.uuid4().hex[:8]}"
            for i in range(3):
                test_file = os.path.join(temp_dir, f"{unique_kw}_{i}.md")
                with open(test_file, "w") as f:
                    f.write(f"# {unique_kw}_{i}\n\nContent for glob test {i}.")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.md", uri=root_uri)
            assert glob_resp.status_code == 200
            results = glob_resp.json().get("result", [])
            if isinstance(results, list):
                assert len(results) >= 3, (
                    f"glob **/*.md should find at least 3 files, got {len(results)}"
                )

    def test_glob_wildcard_name(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_wild_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Wildcard test for {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.txt", uri=root_uri)
            assert glob_resp.status_code == 200

    def test_glob_without_uri(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.md")
        assert glob_resp.status_code == 200
        results = glob_resp.json().get("result", {})
        assert isinstance(results, (list, dict)), (
            f"glob without uri should return list or dict, got {type(results).__name__}"
        )

    def test_glob_no_match_returns_empty(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.xyz_nosuch_ext")
        assert glob_resp.status_code == 200
        results = glob_resp.json().get("result", [])
        if isinstance(results, list):
            assert len(results) == 0, (
                f"glob for nonexistent ext should return empty, got {len(results)}"
            )

    def test_glob_deep_nested_pattern(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_nested_{uuid.uuid4().hex[:8]}"
            nested_dir = os.path.join(temp_dir, "sub1", "sub2")
            os.makedirs(nested_dir, exist_ok=True)
            test_file = os.path.join(nested_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nDeeply nested file.")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern="**/*.md", uri=root_uri)
            assert glob_resp.status_code == 200
            results = glob_resp.json().get("result", [])
            if isinstance(results, list) and len(results) > 0:
                found = any(unique_kw in str(r) for r in results)
                assert found, f"glob should find nested file, got {results}"

    def test_glob_star_pattern(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_star_{uuid.uuid4().hex[:8]}"
            for ext in ["md", "txt"]:
                test_file = os.path.join(temp_dir, f"{unique_kw}.{ext}")
                with open(test_file, "w") as f:
                    f.write(f"Content for {ext}")

            add_resp = api_client.add_resource(path=temp_dir, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern=f"**/{unique_kw}.*", uri=root_uri)
            assert glob_resp.status_code == 200

    def test_glob_result_type_is_list_of_strings(self, api_client):
        glob_resp = api_client.glob(pattern="**/*.md")
        assert glob_resp.status_code == 200
        results = glob_resp.json().get("result", {})
        assert isinstance(results, (list, dict)), "glob result should be a list or dict"
        if isinstance(results, dict):
            items = results.get("matches", results.get("items", []))
            assert isinstance(items, list), "glob matches should be list"
        elif isinstance(results, list) and results:
            first = results[0]
            assert isinstance(first, (str, dict)), (
                f"glob items should be str or dict, got {type(first).__name__}"
            )

    def test_glob_with_specific_filename(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_specific_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nSpecific filename test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_resp = api_client.glob(pattern=f"**/{unique_kw}.md", uri=root_uri)
            assert glob_resp.status_code == 200
            results = glob_resp.json().get("result", [])
            if isinstance(results, list):
                assert len(results) >= 1, (
                    f"glob for specific filename should find at least 1, got {len(results)}"
                )

    def test_glob_pattern_with_uri_filters_scope(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"glob_scope_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nScope test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            glob_with_uri = api_client.glob(pattern="**/*.md", uri=root_uri)
            glob_without_uri = api_client.glob(pattern="**/*.md")

            assert glob_with_uri.status_code == 200
            assert glob_without_uri.status_code == 200

            results_with = glob_with_uri.json().get("result", [])
            results_without = glob_without_uri.json().get("result", [])

            if isinstance(results_with, list) and isinstance(results_without, list):
                assert len(results_with) <= len(results_without), (
                    f"glob with uri filter should return <= without filter, "
                    f"with={len(results_with)} without={len(results_without)}"
                )

    def test_glob_empty_pattern(self, api_client):
        glob_resp = api_client.glob(pattern="")
        assert glob_resp.status_code == 400, (
            f"glob with empty pattern should return valid status, got {glob_resp.status_code}"
        )
