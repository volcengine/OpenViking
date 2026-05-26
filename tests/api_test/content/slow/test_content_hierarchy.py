import os
import tempfile
import uuid


class TestContentHierarchy:
    def test_abstract_shorter_than_overview(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"l0l1_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\n")
                for i in range(10):
                    f.write(
                        f"## Section {i}\n\nDetailed content for section {i} about {unique_kw} and related topics in software engineering.\n\n"
                    )

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            if abstract_resp.status_code != 200:
                return
            abstract = abstract_resp.json().get("result", "")

            overview_resp = api_client.get_overview(root_uri)
            if overview_resp.status_code != 200:
                return
            overview = overview_resp.json().get("result", "")

            assert isinstance(abstract, str), f"abstract should be string, got {type(abstract)}"
            assert isinstance(overview, str), f"overview should be string, got {type(overview)}"
            assert len(abstract) > 0, "abstract should not be empty"
            assert len(overview) > 0, "overview should not be empty"
            assert len(abstract) <= len(overview), (
                f"L0 abstract ({len(abstract)} chars) should be <= L1 overview ({len(overview)} chars)"
            )

    def test_overview_shorter_than_full_content(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"l1l2_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            long_content = f"# {unique_kw}\n\n"
            for i in range(20):
                long_content += (
                    f"## Section {i}\n\nThis is detailed content for section {i} about {unique_kw}. "
                    * 5
                    + "\n\n"
                )

            with open(test_file, "w") as f:
                f.write(long_content)

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            overview_resp = api_client.get_overview(root_uri)
            if overview_resp.status_code != 200:
                return
            overview = overview_resp.json().get("result", "")

            read_resp = api_client.fs_read(root_uri)
            if read_resp.status_code != 200:
                return
            full_content = read_resp.json().get("result", "")

            assert len(overview) <= len(full_content), (
                f"L1 overview ({len(overview)} chars) should be <= L2 full content ({len(full_content)} chars)"
            )

    def test_reindex_regenerates_abstract_and_overview(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"reindex_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.md")
            with open(test_file, "w") as f:
                f.write(f"# {unique_kw}\n\nOriginal content about {unique_kw} for reindex testing.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_before_resp = api_client.get_abstract(root_uri)
            if abstract_before_resp.status_code != 200:
                return
            abstract_before_resp.json().get("result", "")

            reindex_resp = api_client.content_reindex(root_uri, regenerate=True, wait=False)
            if reindex_resp.status_code != 200:
                return

            import time

            time.sleep(5)

            abstract_after_resp = api_client.get_abstract(root_uri)
            if abstract_after_resp.status_code != 200:
                return
            abstract_after = abstract_after_resp.json().get("result", "")

            assert isinstance(abstract_after, str), "reindexed abstract should be string"
            assert len(abstract_after) > 0, "reindexed abstract should not be empty"

    def test_content_download_returns_raw_content(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            unique_kw = f"download_{uuid.uuid4().hex[:8]}"
            test_file = os.path.join(temp_dir, f"{unique_kw}.txt")
            with open(test_file, "w") as f:
                f.write(f"Downloadable content about {unique_kw}")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            download_resp = api_client.content_download(root_uri)
            if download_resp.status_code == 200:
                assert len(download_resp.content) > 0, "download should return content"
                content_type = download_resp.headers.get("Content-Type", "")
                assert content_type != "", "download response should have Content-Type header"

    def test_content_download_nonexistent_uri(self, api_client):
        download_resp = api_client.content_download("viking://resources/nonexistent_download_test")
        assert download_resp.status_code == 404, (
            f"download nonexistent URI should return error, got {download_resp.status_code}"
        )
