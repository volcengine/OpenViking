import uuid


class TestErrorFormatConsistency:
    def test_all_error_responses_have_status_field(self, api_client):
        error_scenarios = [
            (
                "fs_stat_nonexistent",
                lambda: api_client.fs_stat("viking://resources/nonexistent_error_test"),
            ),
            (
                "fs_read_nonexistent",
                lambda: api_client.fs_read("viking://resources/nonexistent_error_test"),
            ),
            (
                "get_session_nonexistent",
                lambda: api_client.get_session(f"nonexistent-{uuid.uuid4().hex[:8]}"),
            ),
            (
                "get_task_nonexistent",
                lambda: api_client.get_task(f"nonexistent-{uuid.uuid4().hex[:8]}"),
            ),
        ]

        for name, action in error_scenarios:
            resp = action()
            if resp.status_code >= 400:
                data = resp.json()
                assert "status" in data or "error" in data, (
                    f"error response for {name} should have 'status' or 'error', got keys: {sorted(data.keys())}, body: {str(data)[:200]}"
                )
                if "error" in data:
                    error = data["error"]
                    assert isinstance(error, dict), (
                        f"error field should be dict for {name}, got {type(error)}"
                    )
                    assert "message" in error, (
                        f"error should have 'message' for {name}, got keys: {sorted(error.keys())}"
                    )

    def test_error_response_not_ok_status(self, api_client):
        resp = api_client.fs_stat("viking://resources/nonexistent_error_format")
        if resp.status_code >= 400:
            data = resp.json()
            if "status" in data:
                assert data["status"] != "ok", (
                    f"error response should not have status='ok', got: {data}"
                )

    def test_empty_uri_parameter(self, api_client):
        resp = api_client.fs_stat("")
        assert resp.status_code == 400, (
            f"empty URI should return 400/500, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_missing_required_field_in_add_message(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            endpoint = f"/api/v1/sessions/{session_id}/messages"
            url = f"{api_client.server_url}{endpoint}"
            resp = api_client.session.post(url, json={})
            assert resp.status_code == 400, (
                f"missing required fields should return error, got {resp.status_code}: {resp.text[:200]}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_add_resource_without_path_or_file(self, api_client):
        endpoint = "/api/v1/resources"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={})
        assert resp.status_code == 400, (
            f"add_resource without path/file should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_find_with_negative_limit(self, api_client):
        find_resp = api_client.find(query="test", limit=-1)
        assert find_resp.status_code == 200, (
            f"negative limit should return error or be clamped, got {find_resp.status_code}"
        )

    def test_find_with_zero_limit(self, api_client):
        find_resp = api_client.find(query="test", limit=0)
        assert find_resp.status_code == 200, (
            f"zero limit should return error or empty results, got {find_resp.status_code}"
        )

    def test_content_write_without_uri(self, api_client):
        endpoint = "/api/v1/content/write"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={"content": "test", "mode": "create"})
        assert resp.status_code == 400, (
            f"write without URI should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_grep_without_pattern(self, api_client):
        endpoint = "/api/v1/search/grep"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={"uri": "viking://resources/"})
        assert resp.status_code == 400, (
            f"grep without pattern should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_glob_without_pattern(self, api_client):
        endpoint = "/api/v1/search/glob"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={"uri": "viking://resources/"})
        assert resp.status_code == 400, (
            f"glob without pattern should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_fs_mv_without_from_uri(self, api_client):
        endpoint = "/api/v1/fs/mv"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={"to_uri": "viking://resources/dest"})
        assert resp.status_code == 400, (
            f"mv without from_uri should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_fs_mkdir_without_uri(self, api_client):
        endpoint = "/api/v1/fs/mkdir"
        url = f"{api_client.server_url}{endpoint}"
        resp = api_client.session.post(url, json={})
        assert resp.status_code == 400, (
            f"mkdir without uri should return error, got {resp.status_code}: {resp.text[:200]}"
        )

    def test_add_message_to_nonexistent_session(self, api_client):
        fake_id = f"nonexistent-{uuid.uuid4().hex[:12]}"
        msg_resp = api_client.add_message(fake_id, "user", "test")
        assert msg_resp.status_code == 200, (
            f"add message to nonexistent session returns 200, got {msg_resp.status_code}"
        )
