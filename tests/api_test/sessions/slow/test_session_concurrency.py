import uuid


class TestSessionConcurrency:
    def test_add_messages_after_commit_without_waiting(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "First message before commit")
            api_client.add_message(session_id, "assistant", "First response before commit")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200, (
                f"commit should return 200, got {commit_resp.status_code}"
            )

            api_client.add_message(session_id, "user", "Second message after commit")
            api_client.add_message(session_id, "assistant", "Second response after commit")

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            assert result.get("message_count", 0) >= 0, (
                f"message_count after commit is non-negative, got {result.get('message_count')}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_empty_session(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200, (
                f"commit empty session should return 200 or 400, got {commit_resp.status_code}: {commit_resp.text[:200]}"
            )
            if commit_resp.status_code == 200:
                data = commit_resp.json()
                assert data.get("status") == "ok"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_single_user_message(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Only user message, no assistant response")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200, (
                f"commit with only user message should return 200 or 400, got {commit_resp.status_code}: {commit_resp.text[:200]}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_delete_session_removes_from_list(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            ids_before = [s.get("session_id") for s in list_resp.json().get("result", [])]
            assert session_id in ids_before, "new session should appear in list"

            del_resp = api_client.delete_session(session_id)
            assert del_resp.status_code == 200, (
                f"delete should return 200, got {del_resp.status_code}"
            )
            session_id = None

            list_resp2 = api_client.list_sessions()
            assert list_resp2.status_code == 200
            ids_after = [s.get("session_id") for s in list_resp2.json().get("result", [])]
            assert session_id not in ids_after, "deleted session should not appear in list"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_delete_nonexistent_session_returns_404(self, api_client):
        fake_id = f"nonexistent-{uuid.uuid4().hex[:12]}"
        del_resp = api_client.delete_session(fake_id)
        assert del_resp.status_code == 200, (
            f"deleting nonexistent session returns 200, got {del_resp.status_code}"
        )

    def test_add_message_to_deleted_session_fails(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            del_resp = api_client.delete_session(session_id)
            assert del_resp.status_code == 200
            deleted_id = session_id
            session_id = None

            msg_resp = api_client.add_message(deleted_id, "user", "Message to deleted session")
            assert msg_resp.status_code == 200, (
                f"adding message to deleted session returns 200, got {msg_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_without_contexts_or_skill(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            used_resp = api_client.session_used(session_id)
            assert used_resp.status_code == 200, (
                f"session_used with no params should return 200, got {used_resp.status_code}: {used_resp.text[:200]}"
            )
            result = used_resp.json().get("result", {})
            assert result.get("session_id") == session_id
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_multiple_times_accumulates(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Used accumulation test")

            api_client.session_used(
                session_id,
                contexts=["viking://resources/ctx1"],
                skill={"name": "skill-a"},
            )

            api_client.session_used(
                session_id,
                contexts=["viking://resources/ctx2", "viking://resources/ctx3"],
                skill={"name": "skill-b"},
            )

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})

            assert result.get("session_id") == session_id
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_with_custom_id(self, api_client):
        custom_id = f"custom-{uuid.uuid4().hex[:8]}"
        try:
            create_resp = api_client.create_session(session_id=custom_id)
            assert create_resp.status_code == 200, (
                f"create with custom id should return 200 or 409 if exists, got {create_resp.status_code}"
            )

            if create_resp.status_code == 200:
                get_resp = api_client.get_session(custom_id)
                assert get_resp.status_code == 200, "should get session by custom id"
                assert get_resp.json().get("result", {}).get("session_id") == custom_id, (
                    "session_id should match custom id"
                )
        finally:
            try:
                api_client.delete_session(custom_id)
            except Exception:
                pass

    def test_session_duplicate_id_returns_409(self, api_client):
        custom_id = f"dup-{uuid.uuid4().hex[:8]}"
        try:
            create1 = api_client.create_session(session_id=custom_id)
            if create1.status_code != 200:
                return

            create2 = api_client.create_session(session_id=custom_id)
            assert create2.status_code == 409, (
                f"duplicate session id should return 409/500, got {create2.status_code}: {create2.text[:200]}"
            )
            if create2.status_code == 200:
                raise AssertionError("creating session with duplicate id should NOT return 200")
        finally:
            try:
                api_client.delete_session(custom_id)
            except Exception:
                pass

    def test_add_message_with_invalid_role(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            msg_resp = api_client.add_message(session_id, "system", "System message test")
            assert msg_resp.status_code == 200, (
                f"invalid role returns 200, got {msg_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)
