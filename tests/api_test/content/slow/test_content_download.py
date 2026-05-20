class TestContentDownload:
    def test_download_nonexistent_uri(self, api_client):
        dl_resp = api_client.content_download("viking://resources/nonexistent_file_xyz")
        assert dl_resp.status_code == 404, (
            f"Expected 404 or 500 for nonexistent URI, got {dl_resp.status_code}"
        )
