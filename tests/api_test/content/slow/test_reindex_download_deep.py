import os
import tempfile
import uuid


class TestReindexDownloadDeep:
    def test_download_nonexistent_uri(self, api_client):
        dl_resp = api_client.content_download("viking://resources/nonexistent_dl_test_99999.md")
        assert dl_resp.status_code == 404, (
            f"download nonexistent should return error, got {dl_resp.status_code}"
        )

    def test_reindex_then_find_still_works(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            kw = f"reidx_find_{uuid.uuid4().hex[:8]}"
            f1 = os.path.join(temp_dir, f"{kw}.md")
            with open(f1, "w") as f:
                f.write(f"# {kw}\n\nContent about functional programming paradigms.")

            add_resp = api_client.add_resource(path=f1, wait=True)
            if add_resp.status_code != 200:
                return
            root_uri = add_resp.json()["result"]["root_uri"]

            api_client.content_reindex(root_uri, regenerate=False, wait=True)

            find_resp = api_client.find(query=kw, limit=5)
            assert find_resp.status_code == 200
            resources = find_resp.json().get("result", {}).get("resources", [])
            assert len(resources) > 0, (
                f"resource should still be findable after reindex, query={kw}"
            )
