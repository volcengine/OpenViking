import os
import tempfile


class TestContentDeep:
    def test_abstract_returns_string(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "abstract_test.txt")
            with open(test_file, "w") as f:
                f.write("Content for abstract test. This should be summarized.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            abstract_resp = api_client.get_abstract(root_uri)
            assert abstract_resp.status_code == 200, (
                f"abstract should return 200, got {abstract_resp.status_code}: {abstract_resp.text[:200]}"
            )
            abstract_data = abstract_resp.json()
            assert abstract_data.get("status") == "ok"
            result = abstract_data.get("result")
            assert result is not None, "abstract should not be None"
            assert isinstance(result, str), f"abstract should be string, got {type(result)}"
            assert len(result) > 0, "abstract should not be empty"

    def test_overview_returns_string(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "overview_test.txt")
            with open(test_file, "w") as f:
                f.write("Content for overview test.")

            add_resp = api_client.add_resource(path=test_file, wait=True)
            assert add_resp.status_code == 200, (
                f"add_resource should return 200, got {add_resp.status_code}"
            )
            root_uri = add_resp.json()["result"]["root_uri"]

            overview_resp = api_client.get_overview(root_uri)
            assert overview_resp.status_code == 200, (
                f"overview should return 200, got {overview_resp.status_code}: {overview_resp.text[:200]}"
            )
            overview_data = overview_resp.json()
            assert overview_data.get("status") == "ok"
            result = overview_data.get("result")
            assert result is not None, "overview should not be None"
            assert isinstance(result, str), f"overview should be string, got {type(result)}"

    def test_abstract_nonexistent_uri(self, api_client):
        abstract_resp = api_client.get_abstract("viking://resources/nonexistent_abstract")
        assert abstract_resp.status_code == 404, (
            f"abstract of nonexistent URI should return 404/500, got {abstract_resp.status_code}"
        )

    def test_overview_nonexistent_uri(self, api_client):
        overview_resp = api_client.get_overview("viking://resources/nonexistent_overview")
        assert overview_resp.status_code == 404, (
            f"overview of nonexistent URI should return 404/500, got {overview_resp.status_code}"
        )
