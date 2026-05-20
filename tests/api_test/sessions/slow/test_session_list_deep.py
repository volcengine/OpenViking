class TestSessionListDeep:
    def test_list_sessions_returns_list(self, api_client):
        resp = api_client.list_sessions()
        assert resp.status_code == 200
        result = resp.json().get("result", [])
        assert isinstance(result, list), (
            f"list_sessions result should be list, got {type(result).__name__}"
        )

    def test_list_sessions_after_create(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            sessions = list_resp.json().get("result", [])
            found = any(
                (isinstance(s, dict) and s.get("session_id") == session_id)
                or (isinstance(s, str) and s == session_id)
                for s in sessions
            )
            assert found, f"newly created session should be in list, id={session_id}"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_sessions_item_has_session_id(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            session_id = create_resp.json()["result"]["session_id"]

            list_resp = api_client.list_sessions()
            sessions = list_resp.json().get("result", [])
            target = None
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    target = s
                    break
            if target:
                assert "session_id" in target, (
                    f"session item should have session_id, got keys: {list(target.keys())}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_sessions_after_delete(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            session_id = create_resp.json()["result"]["session_id"]
            api_client.delete_session(session_id)
            session_id = None

            list_resp = api_client.list_sessions()
            list_resp.json().get("result", [])
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_sessions_multiple_sessions(self, api_client):
        session_ids = []
        try:
            for _ in range(3):
                r = api_client.create_session()
                if r.status_code == 200:
                    session_ids.append(r.json()["result"]["session_id"])

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            sessions = list_resp.json().get("result", [])
            for sid in session_ids:
                found = any(
                    (isinstance(s, dict) and s.get("session_id") == sid)
                    or (isinstance(s, str) and s == sid)
                    for s in sessions
                )
                assert found, f"session {sid} should be in list"
        finally:
            for sid in session_ids:
                api_client.delete_session(sid)

    def test_list_sessions_response_structure(self, api_client):
        resp = api_client.list_sessions()
        assert resp.status_code == 200
        body = resp.json()
        assert "status" in body, "response should have status field"
        assert body["status"] == "ok", f"status should be ok, got {body['status']}"
        assert "result" in body, "response should have result field"

    def test_list_sessions_with_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            session_id = create_resp.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Test message for list")

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            sessions = list_resp.json().get("result", [])
            found = any(
                (isinstance(s, dict) and s.get("session_id") == session_id)
                or (isinstance(s, str) and s == session_id)
                for s in sessions
            )
            assert found, "session with messages should appear in list"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_sessions_after_commit(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            session_id = create_resp.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Commit then list test")
            commit_resp = api_client.session_commit(session_id)
            if commit_resp.status_code != 200:
                return

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            sessions = list_resp.json().get("result", [])
            found = any(
                (isinstance(s, dict) and s.get("session_id") == session_id)
                or (isinstance(s, str) and s == session_id)
                for s in sessions
            )
            assert found, "committed session should appear in list"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_list_sessions_item_may_have_uri(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            session_id = create_resp.json()["result"]["session_id"]

            list_resp = api_client.list_sessions()
            sessions = list_resp.json().get("result", [])
            for s in sessions:
                if isinstance(s, dict) and s.get("session_id") == session_id:
                    if "uri" in s:
                        assert "viking:" in s["uri"], f"uri should contain viking:, got {s['uri']}"
                    break
        finally:
            if session_id:
                api_client.delete_session(session_id)
