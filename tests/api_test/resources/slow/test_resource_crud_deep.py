import os
import tempfile
import uuid


class TestResourceCrudDeep:
    def test_add_resource_then_stat(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"crud_stat_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nStat test content.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            assert "name" in result or "uri" in result, (
                f"stat should return name or uri, got keys: {list(result.keys())}"
            )

    def test_add_resource_then_abstract(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"crud_abstract_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nAbstract test content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200
            abstract = abstract_resp.json().get("result", "")
            if abstract:
                assert isinstance(abstract, str), f"abstract should be string, got {type(abstract)}"

    def test_add_resource_then_overview(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"crud_overview_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nOverview test content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            overview_resp = api_client.get_overview(root_uri)
            assert overview_resp.status_code == 200
            overview = overview_resp.json().get("result", "")
            if overview:
                assert isinstance(overview, str), f"overview should be string, got {type(overview)}"

    def test_add_resource_then_find(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"crud_find_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nFind test content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return

            find_resp = api_client.find(query=unique_kw, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) >= 1, (
                f"find should return at least 1 result for unique keyword, got {len(resources)}"
            )

    def test_add_resource_then_grep(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"crud_grep_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nGrep test content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            grep_resp = api_client.grep(root_uri, pattern=unique_kw)
            assert grep_resp.status_code == 200
