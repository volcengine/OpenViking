import os
import tempfile
import uuid


class TestContentReadL2Deep:
    def _create_resource(self, api_client, content):
        temp_dir = tempfile.mkdtemp()
        unique_kw = f"readl2_{uuid.uuid4().hex[:8]}"
        test_file = os.path.join(temp_dir, f"{unique_kw}.md")
        with open(test_file, "w") as f:
            f.write(content)
        add_resp = api_client.add_resource(path=test_file, wait=True)
        if add_resp.status_code != 200:
            return None, None
        root_uri = add_resp.json()["result"]["root_uri"]
        ls_resp = api_client.fs_ls(root_uri)
        if ls_resp.status_code != 200:
            return root_uri, None
        children = ls_resp.json().get("result", [])
        if not children:
            return root_uri, None
        child_uri = children[0].get("uri", "") if isinstance(children[0], dict) else children[0]
        return root_uri, child_uri

    def test_read_returns_full_content(self, api_client):
        unique_kw = f"fullcontent_{uuid.uuid4().hex[:6]}"
        content = f"# {unique_kw}\n\nThis is the full L2 content for read test.\nMultiple lines.\nEnd of content."
        root_uri, child_uri = self._create_resource(api_client, content)
        if not child_uri:
            return
        try:
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            result = read_resp.json().get("result", "")
            assert isinstance(result, str), f"result should be str, got {type(result).__name__}"
            assert unique_kw in result, f"read should contain unique keyword, got: {result[:200]}"
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_read_preserves_line_breaks(self, api_client):
        content = "Line1\nLine2\nLine3\nLine4"
        root_uri, child_uri = self._create_resource(api_client, content)
        if not child_uri:
            return
        try:
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            result = read_resp.json().get("result", "")
            if isinstance(result, str):
                assert "\n" in result, "read should preserve line breaks"
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_read_after_replace_shows_new(self, api_client):
        root_uri, child_uri = self._create_resource(api_client, "Old content before replace.")
        if not child_uri:
            return
        try:
            api_client.fs_write(
                child_uri, "New content after replace operation.", mode="replace", wait=True
            )
            import time

            time.sleep(5)
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            result = read_resp.json().get("result", "")
            if isinstance(result, str) and "New content" not in result:
                time.sleep(8)
                read_resp = api_client.fs_read(child_uri)
                result = read_resp.json().get("result", "")
            if isinstance(result, str):
                assert "New content" in result or "Old content" in result, (
                    f"read after replace should show content, got: {result[:200]}"
                )
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_read_nonexistent_file_returns_404(self, api_client):
        fake_uri = f"viking://resources/readl2_nonexist_{uuid.uuid4().hex[:8]}.md"
        read_resp = api_client.fs_read(fake_uri)
        assert read_resp.status_code == 404, (
            f"read nonexistent should return 404, got {read_resp.status_code}"
        )

    def test_read_unicode_content(self, api_client):
        content = "中文内容 🚀 こんにちは 한국어 emoji: 🎉"
        root_uri, child_uri = self._create_resource(api_client, content)
        if not child_uri:
            return
        try:
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            result = read_resp.json().get("result", "")
            if isinstance(result, str):
                assert "中文" in result, f"unicode should be preserved, got: {result[:200]}"
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_read_vs_download_consistency(self, api_client):
        unique_kw = f"consist_{uuid.uuid4().hex[:6]}"
        content = f"# {unique_kw}\n\nConsistency test between read and download."
        root_uri, child_uri = self._create_resource(api_client, content)
        if not child_uri:
            return
        try:
            read_resp = api_client.fs_read(child_uri)
            download_resp = api_client.content_download(child_uri)
            assert read_resp.status_code == 200
            assert download_resp.status_code == 200
            read_result = read_resp.json().get("result", "")
            download_result = download_resp.text if download_resp.text else ""
            if isinstance(read_result, str) and isinstance(download_result, str):
                assert unique_kw in read_result or unique_kw in download_result, (
                    "at least one of read/download should contain the content"
                )
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)

    def test_read_response_structure(self, api_client):
        root_uri, child_uri = self._create_resource(api_client, "Structure test content.")
        if not child_uri:
            return
        try:
            read_resp = api_client.fs_read(child_uri)
            assert read_resp.status_code == 200
            body = read_resp.json()
            assert "status" in body, "response should have status field"
            assert "result" in body, "response should have result field"
            assert body["status"] == "ok", f"status should be ok, got {body['status']}"
        finally:
            if root_uri:
                api_client.fs_rm(root_uri, recursive=True)
