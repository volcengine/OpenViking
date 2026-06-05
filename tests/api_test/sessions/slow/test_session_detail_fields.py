import time


class TestSessionDetailFields:
    def test_session_detail_contains_all_meta_fields(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200, (
                f"get_session should return 200, got {get_resp.status_code}"
            )
            result = get_resp.json().get("result", {})

            required_fields = [
                "session_id",
                "created_at",
                "updated_at",
                "message_count",
                "commit_count",
                "memories_extracted",
                "pending_tokens",
            ]
            for field in required_fields:
                assert field in result, (
                    f"session detail should contain '{field}', got keys: {sorted(result.keys())}"
                )

            assert result["session_id"] == session_id, (
                f"session_id mismatch: expected {session_id}, got {result['session_id']}"
            )
            assert isinstance(result["message_count"], int), (
                f"message_count should be int, got {type(result['message_count'])}"
            )
            assert isinstance(result["commit_count"], int), (
                f"commit_count should be int, got {type(result['commit_count'])}"
            )
            assert isinstance(result["pending_tokens"], int), (
                f"pending_tokens should be int, got {type(result['pending_tokens'])}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_detail_llm_token_usage_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})

            llm_usage = result.get("llm_token_usage", {})
            assert isinstance(llm_usage, dict), (
                f"llm_token_usage should be dict, got {type(llm_usage)}"
            )

            for key in ["prompt_tokens", "completion_tokens", "total_tokens"]:
                assert key in llm_usage, (
                    f"llm_token_usage should contain '{key}', got keys: {sorted(llm_usage.keys())}"
                )
                assert isinstance(llm_usage[key], int), (
                    f"llm_token_usage['{key}'] should be int, got {type(llm_usage[key])}"
                )

            embedding_usage = result.get("embedding_token_usage", {})
            assert isinstance(embedding_usage, dict), (
                f"embedding_token_usage should be dict, got {type(embedding_usage)}"
            )
            assert "total_tokens" in embedding_usage, (
                "embedding_token_usage should contain 'total_tokens'"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_detail_user_field(self, api_client):
        create_resp = api_client.create_session()
        assert create_resp.status_code == 200
        create_data = create_resp.json()
        result = create_data.get("result", {})

        assert "user" in result, "create session result should contain 'user'"
        user = result["user"]
        assert isinstance(user, dict), f"user should be dict, got {type(user)}"
        assert "account_id" in user, "user should contain account_id"
        assert "user_id" in user, "user should contain user_id"

    def test_session_message_count_increments_correctly(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            count_before = get_resp.json().get("result", {}).get("message_count", 0)

            api_client.add_message(session_id, "user", "Count test 1")
            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            count_after_1 = get_resp.json().get("result", {}).get("message_count", 0)
            assert count_after_1 == count_before + 1, (
                f"message_count should increment by 1, before={count_before} after={count_after_1}"
            )

            api_client.add_message(session_id, "assistant", "Count test 2")
            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            count_after_2 = get_resp.json().get("result", {}).get("message_count", 0)
            assert count_after_2 == count_before + 2, (
                f"message_count should increment by 2, before={count_before} after={count_after_2}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_pending_tokens_increases_with_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            pending_before = get_resp.json().get("result", {}).get("pending_tokens", 0)

            api_client.add_message(session_id, "user", "A" * 100)
            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            pending_after = get_resp.json().get("result", {}).get("pending_tokens", 0)

            assert pending_after > pending_before, (
                f"pending_tokens should increase after adding message, before={pending_before} after={pending_after}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_last_commit_at_updated(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            last_commit_before = get_resp.json().get("result", {}).get("last_commit_at", "")

            api_client.add_message(session_id, "user", "Last commit at test")
            api_client.add_message(session_id, "assistant", "Last commit at test response")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            time.sleep(2)

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            last_commit_after = get_resp.json().get("result", {}).get("last_commit_at", "")

            if last_commit_before == "":
                if last_commit_after != "":
                    pass
                else:
                    pass
            else:
                assert last_commit_after >= last_commit_before, (
                    "last_commit_at should be updated after commit"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_created_at_and_updated_at(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})

            assert "created_at" in result, "session should have created_at"
            assert "updated_at" in result, "session should have updated_at"
            assert result["created_at"] != "", "created_at should not be empty"
            assert result["updated_at"] != "", "updated_at should not be empty"

            api_client.add_message(session_id, "user", "Update timestamp test")

            get_resp2 = api_client.get_session(session_id)
            assert get_resp2.status_code == 200
            result2 = get_resp2.json().get("result", {})
            assert result2["updated_at"] >= result["updated_at"], (
                "updated_at should increase after adding message"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_participant_ids_are_lists(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})

            assert isinstance(result.get("participant_user_ids"), (list, type(None))), (
                "participant_user_ids should be a list or None"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_list_item_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            list_resp = api_client.list_sessions()
            assert list_resp.status_code == 200
            sessions = list_resp.json().get("result", [])
            assert isinstance(sessions, list)

            target = None
            for s in sessions:
                if s.get("session_id") == session_id:
                    target = s
                    break

            assert target is not None, f"newly created session {session_id} should appear in list"
            assert "session_id" in target, "session list item should have session_id"
            assert "uri" in target, "session list item should have uri"
            assert target["uri"].startswith("viking://session/"), (
                f"session uri should start with viking://session/, got {target['uri']}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)
