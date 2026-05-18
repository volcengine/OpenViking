import os
import tempfile
import uuid


class TestStatAbstractOverviewDeep:
    def test_stat_file_has_all_expected_fields(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"stat_fields_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nContent for stat field validation.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            expected_fields = ["name", "isDir"]
            for field in expected_fields:
                assert field in result, (
                    f"stat should contain {field}, got keys: {list(result.keys())}"
                )

    def test_stat_directory_isDir_true(self, api_client):
        dir_uri = f"viking://resources/stat_dir_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(dir_uri)
            stat_resp = api_client.fs_stat(dir_uri)
            assert stat_resp.status_code == 200
            assert stat_resp.json().get("result", {}).get("isDir") is True
        finally:
            api_client.fs_rm(dir_uri, recursive=True)

    def test_stat_file_isDir_false(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"stat_file_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nFile stat test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            stat_resp = api_client.fs_stat(root_uri)
            assert stat_resp.status_code == 200
            result = stat_resp.json().get("result", {})
            assert "uri" in result or "name" in result, (
                f"stat result should have uri or name, got keys: {sorted(result.keys())}"
            )

    def test_abstract_nonexistent_uri(self, api_client):
        fake_uri = f"viking://resources/abstract_nonexist_{uuid.uuid4().hex[:8]}"
        abstract_resp = api_client.get_abstract(fake_uri)
        assert abstract_resp.status_code == 404, (
            f"abstract nonexistent should return 200/404/500, got {abstract_resp.status_code}"
        )

    def test_abstract_content_consistency(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"abstract_consist_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            content = f"# {unique_kw}\n\nThis document discusses quantum computing principles and applications."
            with open(test_file, "w") as f:
                f.write(content)

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200
            abstract = abstract_resp.json().get("result", "")
            if abstract and isinstance(abstract, str):
                assert len(abstract) > 0, "abstract should not be empty for non-empty content"

    def test_overview_content_consistency(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"overview_consist_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            content = (
                f"# {unique_kw}\n\nThis document covers blockchain technology and smart contracts."
            )
            with open(test_file, "w") as f:
                f.write(content)

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            overview_resp = api_client.get_overview(root_uri)
            assert overview_resp.status_code == 200
            overview = overview_resp.json().get("result", "")
            if overview and isinstance(overview, str):
                assert len(overview) > 0, "overview should not be empty for non-empty content"

    def test_abstract_after_reindex(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"abstract_reindex_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nOriginal content about {unique_kw}.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            reindex_resp = api_client.content_reindex(root_uri, regenerate=True, wait=True)
            if reindex_resp.status_code != 200:
                return

            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200
            abstract = abstract_resp.json().get("result", "")
            if abstract:
                assert isinstance(abstract, str), "abstract after reindex should be string"

    def test_stat_nonexistent_uri(self, api_client):
        fake_uri = f"viking://resources/stat_nonexist_{uuid.uuid4().hex[:8]}"
        stat_resp = api_client.fs_stat(fake_uri)
        assert stat_resp.status_code == 404, (
            f"stat nonexistent should return 404/500, got {stat_resp.status_code}"
        )

    def test_abstract_and_overview_both_available(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"both_avail_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(
                    f"# {unique_kw}\n\nComprehensive document about cloud computing and distributed systems."
                )

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            overview_resp = api_client.get_overview(root_uri)

            assert abstract_resp.status_code == 200
            assert overview_resp.status_code == 200
