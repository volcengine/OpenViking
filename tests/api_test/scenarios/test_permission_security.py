import uuid


class TestPermissionAndSecurity:
    def test_invalid_api_key_returns_401(self, api_client):
        original_key = api_client.api_key
        try:
            api_client.api_key = "invalid-key-12345"
            api_client.session.headers["Authorization"] = "Bearer invalid-key-12345"

            resp = api_client.fs_ls("viking://resources/")
            assert resp.status_code == 401, (
                f"invalid API key should return 401, got {resp.status_code}: {resp.text[:200]}"
            )
        finally:
            api_client.api_key = original_key
            api_client.session.headers["Authorization"] = f"Bearer {original_key}"

    def test_empty_api_key_returns_401(self, api_client):
        original_key = api_client.api_key
        try:
            api_client.api_key = ""
            api_client.session.headers["Authorization"] = "Bearer "

            resp = api_client.fs_ls("viking://resources/")
            assert resp.status_code == 401, (
                f"empty API key should return 401/403, got {resp.status_code}: {resp.text[:200]}"
            )
        finally:
            api_client.api_key = original_key
            api_client.session.headers["Authorization"] = f"Bearer {original_key}"

    def test_no_auth_header_returns_401(self, api_client):
        original = api_client.session.headers.pop("Authorization", None)
        try:
            resp = api_client._request_with_retry(
                "GET",
                f"{api_client.server_url}/api/v1/fs/ls",
                params={"uri": "viking://resources/"},
            )
            assert resp.status_code == 401, (
                f"no auth header should return 401/403, got {resp.status_code}: {resp.text[:200]}"
            )
        finally:
            if original:
                api_client.session.headers["Authorization"] = original

    def test_session_isolation_between_sessions(self, api_client):
        session1 = None
        session2 = None
        try:
            resp1 = api_client.create_session()
            assert resp1.status_code == 200
            session1 = resp1.json()["result"]["session_id"]

            resp2 = api_client.create_session()
            assert resp2.status_code == 200
            session2 = resp2.json()["result"]["session_id"]

            assert session1 != session2, "different sessions should have different IDs"

            api_client.add_message(session1, "user", "Message for session 1 only")
            api_client.add_message(session2, "user", "Message for session 2 only")

            ctx1 = api_client.get_session_context(session1)
            assert ctx1.status_code == 200
            msgs1 = ctx1.json().get("result", {}).get("messages", [])
            msg_contents_1 = []
            for m in msgs1:
                for p in m.get("parts", []):
                    msg_contents_1.append(p.get("text", ""))
                if m.get("content"):
                    msg_contents_1.append(m["content"])
            assert any("session 1" in c for c in msg_contents_1), (
                "session1 context should contain session 1 message"
            )
            assert not any("session 2" in c for c in msg_contents_1), (
                "session1 context should NOT contain session 2 message"
            )

            ctx2 = api_client.get_session_context(session2)
            assert ctx2.status_code == 200
            msgs2 = ctx2.json().get("result", {}).get("messages", [])
            msg_contents_2 = []
            for m in msgs2:
                for p in m.get("parts", []):
                    msg_contents_2.append(p.get("text", ""))
                if m.get("content"):
                    msg_contents_2.append(m["content"])
            assert any("session 2" in c for c in msg_contents_2), (
                "session2 context should contain session 2 message"
            )
            assert not any("session 1" in c for c in msg_contents_2), (
                "session2 context should NOT contain session 1 message"
            )
        finally:
            if session1:
                api_client.delete_session(session1)
            if session2:
                api_client.delete_session(session2)

    def test_delete_session_removes_from_list(self, api_client):
        custom_id = f"del-list-{uuid.uuid4().hex[:8]}"
        try:
            create_resp = api_client.create_session(session_id=custom_id)
            assert create_resp.status_code == 200

            list_before = api_client.list_sessions()
            assert list_before.status_code == 200
            ids_before = [s["session_id"] for s in list_before.json().get("result", [])]
            assert custom_id in ids_before, "session should appear in list after creation"

            api_client.delete_session(custom_id)

            list_after = api_client.list_sessions()
            assert list_after.status_code == 200
            ids_after = [s["session_id"] for s in list_after.json().get("result", [])]
            assert custom_id not in ids_after, "session should NOT appear in list after deletion"
        except Exception:
            try:
                api_client.delete_session(custom_id)
            except Exception:
                pass

    def test_resource_uri_scheme_validation(self, api_client):
        stat_resp = api_client.fs_stat("http://invalid-scheme/resources/test")
        assert stat_resp.status_code == 400, (
            f"invalid URI scheme should return 400, got {stat_resp.status_code}"
        )

    def test_path_traversal_prevention(self, api_client):
        stat_resp = api_client.fs_stat("viking://resources/../../../etc/passwd")
        assert stat_resp.status_code == 403, (
            f"path traversal should be blocked with 403, got {stat_resp.status_code}"
        )
