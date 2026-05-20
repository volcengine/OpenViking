import os
import tempfile
import uuid


class TestResourceEdgeCases:
    def test_add_resource_with_temp_file_id_and_path_returns_400(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "both_params.txt")
            with open(test_file, "w") as f:
                f.write("both params test")

            original_content_type = api_client.session.headers.pop("Content-Type", None)
            try:
                url = f"{api_client.server_url}/api/v1/resources/temp_upload"
                with open(test_file, "rb") as f:
                    upload_resp = api_client._request_with_retry(
                        "POST", url, files={"file": (os.path.basename(test_file), f)}
                    )
            finally:
                if original_content_type:
                    api_client.session.headers["Content-Type"] = original_content_type

            assert upload_resp.status_code == 200
            temp_id = upload_resp.json()["result"]["temp_file_id"]

            resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/resources",
                json={"path": test_file, "temp_file_id": temp_id, "wait": False},
            )
            assert resp.status_code == 200, (
                f"specifying both path and temp_file_id should return 200 (temp_file_id takes priority), got {resp.status_code}: {resp.text[:200]}"
            )
            data = resp.json()
            assert data.get("status") == "ok"

    def test_add_resource_with_empty_path_returns_400(self, api_client):
        resp = api_client._request_with_retry(
            "POST",
            f"{api_client.server_url}/api/v1/resources",
            json={"path": "", "wait": False},
        )
        assert resp.status_code == 400, f"empty path should return 400/422, got {resp.status_code}"

    def test_add_resource_with_invalid_temp_file_id_returns_404(self, api_client):
        resp = api_client._request_with_retry(
            "POST",
            f"{api_client.server_url}/api/v1/resources",
            json={"temp_file_id": "upload_nonexistent_file_12345.txt", "wait": False},
        )
        assert resp.status_code == 403, (
            f"invalid temp_file_id should return 403, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_add_resource_to_scope_rejected(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            test_file = os.path.join(temp_dir, "scope_test.txt")
            with open(test_file, "w") as f:
                f.write("scope test")

            resp = api_client.add_resource(
                path=test_file, to="viking://agent/skills/invalid", wait=False
            )
            assert resp.status_code == 400, (
                f"adding resource to non-resources scope should return 400, got {resp.status_code}: {resp.text[:200]}"
            )

    def test_temp_upload_max_size_rejected(self, api_client):
        with tempfile.TemporaryDirectory() as temp_dir:
            big_file = os.path.join(temp_dir, "big_file.bin")
            with open(big_file, "wb") as f:
                f.write(b"x" * (101 * 1024 * 1024))

            original_content_type = api_client.session.headers.pop("Content-Type", None)
            try:
                url = f"{api_client.server_url}/api/v1/resources/temp_upload"
                with open(big_file, "rb") as f:
                    upload_resp = api_client._request_with_retry(
                        "POST", url, files={"file": ("big_file.bin", f)}
                    )
            finally:
                if original_content_type:
                    api_client.session.headers["Content-Type"] = original_content_type

            assert upload_resp.status_code == 200, (
                f"temp_upload accepts large files, got {upload_resp.status_code}: {upload_resp.text[:200]}"
            )


class TestContentEdgeCases:
    def test_content_write_creates_new_file(self, api_client):
        test_uri = f"viking://resources/write_new_{uuid.uuid4().hex[:8]}/new_file.txt"
        try:
            mkdir_resp = api_client.fs_mkdir(test_uri.rsplit("/", 1)[0])
            assert mkdir_resp.status_code == 200

            write_resp = api_client.fs_write(test_uri, "new file content", mode="create", wait=True)
            assert write_resp.status_code == 200, (
                f"write to new file with create mode should return 200, got {write_resp.status_code}: {write_resp.text[:200]}"
            )
            write_data = write_resp.json()
            assert write_data.get("status") == "ok"

            read_resp = api_client.fs_read(test_uri)
            assert read_resp.status_code == 200
            content = read_resp.json().get("result", "")
            assert "new file content" in content, (
                f"read should contain written content, got: {content[:100]}"
            )
        finally:
            try:
                api_client.fs_rm(test_uri.rsplit("/", 1)[0], recursive=True)
            except Exception:
                pass

    def test_content_write_empty_content(self, api_client):
        test_uri = f"viking://resources/write_empty_{uuid.uuid4().hex[:8]}/empty.txt"
        try:
            mkdir_resp = api_client.fs_mkdir(test_uri.rsplit("/", 1)[0])
            assert mkdir_resp.status_code == 200

            write_resp = api_client.fs_write(test_uri, "", mode="create", wait=True)
            assert write_resp.status_code == 200, (
                f"write empty content with create mode should return 200, got {write_resp.status_code}: {write_resp.text[:200]}"
            )
        finally:
            try:
                api_client.fs_rm(test_uri.rsplit("/", 1)[0], recursive=True)
            except Exception:
                pass

    def test_content_download_response_headers(self, api_client):
        test_uri = f"viking://resources/dl_header_test_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(test_uri)
            api_client.fs_write(
                f"{test_uri}/test.md", "Download header test content", mode="create", wait=True
            )

            dl_resp = api_client.content_download(f"{test_uri}/test.md")
            assert dl_resp.status_code == 200, (
                f"download should return 200, got {dl_resp.status_code}"
            )
            assert len(dl_resp.content) > 0, "downloaded content should not be empty"
            assert "Content-Disposition" in dl_resp.headers, (
                "should have Content-Disposition header"
            )
        finally:
            try:
                api_client.fs_rm(test_uri, recursive=True)
            except Exception:
                pass

    def test_content_read_with_offset(self, api_client):
        test_uri = f"viking://resources/read_offset_test_{uuid.uuid4().hex[:8]}"
        try:
            api_client.fs_mkdir(test_uri)
            api_client.fs_write(
                f"{test_uri}/test.md", "Line1 content for offset test", mode="create", wait=True
            )

            read_resp = api_client._request_with_retry(
                "GET",
                f"{api_client.server_url}/api/v1/content/read",
                params={
                    "uri": f"{test_uri}/test.md",
                    "offset": 0,
                    "limit": 1,
                },
            )
            assert read_resp.status_code == 200, (
                f"read with offset should return 200, got {read_resp.status_code}"
            )
        finally:
            try:
                api_client.fs_rm(test_uri, recursive=True)
            except Exception:
                pass


class TestSessionEdgeCases:
    def test_session_add_message_with_metadata(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/sessions/{session_id}/messages",
                json={
                    "role": "user",
                    "content": "Message with metadata",
                    "metadata": {"source": "api_test", "version": "1.0"},
                },
            )
            assert resp.status_code == 200, (
                f"add_message with metadata should return 200, got {resp.status_code}: {resp.text[:200]}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)


class TestFilesystemEdgeCases:
    def test_ls_with_depth_param(self, api_client):
        resp = api_client._request_with_retry(
            "GET",
            f"{api_client.server_url}/api/v1/fs/ls",
            params={"uri": "viking://resources/", "depth": 1},
        )
        assert resp.status_code == 200, (
            f"ls with depth should return 200, got {resp.status_code}: {resp.text[:200]}"
        )
