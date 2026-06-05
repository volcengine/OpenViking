class TestSessionCommitUsedContext:
    def test_session_commit_flow(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I prefer Python for scripting.")
            api_client.add_message(session_id, "assistant", "Python is great for scripting.")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            commit_data = commit_resp.json()
            assert commit_data.get("status") == "ok"
            result = commit_data.get("result", {})
            assert "session_id" in result
            assert result["session_id"] == session_id
            assert "task_id" in result
            if result.get("task_id"):
                assert isinstance(result["task_id"], str)
                assert len(result["task_id"]) > 0
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_commit_returns_task_id(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I love machine learning.")
            api_client.add_message(session_id, "assistant", "ML is fascinating!")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            commit_data = commit_resp.json()
            assert commit_data.get("status") == "ok"
            result = commit_data.get("result", {})
            assert "session_id" in result
            assert result["session_id"] == session_id

            if "task_id" in result:
                assert isinstance(result["task_id"], str)
                assert len(result["task_id"]) > 0
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_commit_empty_session(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            data = commit_resp.json()
            assert data.get("status") == "ok"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_records_usage(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Test used tracking")

            used_resp = api_client.session_used(
                session_id,
                contexts=["viking://resources/some_context"],
                skill={"name": "test-skill"},
            )
            assert used_resp.status_code == 200
            used_data = used_resp.json()
            assert used_data.get("status") == "ok"
            result = used_data.get("result", {})
            assert result.get("session_id") == session_id
            assert "contexts_used" in result
            assert "skills_used" in result
            assert isinstance(result["contexts_used"], (int, list))
            assert isinstance(result["skills_used"], (int, list))
            ctx_used = result["contexts_used"]
            if isinstance(ctx_used, int):
                assert ctx_used >= 1
            elif isinstance(ctx_used, list):
                assert len(ctx_used) >= 1
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_with_multiple_contexts(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Multi context test")

            used_resp = api_client.session_used(
                session_id,
                contexts=[
                    "viking://resources/context1",
                    "viking://resources/context2",
                    "viking://user/skills/skill1",
                ],
                skill={"name": "multi-context-skill", "uri": "viking://user/skills/multi"},
            )
            assert used_resp.status_code == 200
            data = used_resp.json()
            assert data.get("status") == "ok"
            result = data.get("result", {})
            assert result.get("session_id") == session_id
            assert "contexts_used" in result
            assert "skills_used" in result
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_without_contexts_or_skill(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            used_resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/sessions/{session_id}/used",
                json={},
            )
            assert used_resp.status_code == 200
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_session_context_empty(self, api_client):
        session_id = None
        try:
            resp = api_client.create_session()
            assert resp.status_code == 200
            session_id = resp.json()["result"]["session_id"]

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            ctx_data = ctx_resp.json()
            assert ctx_data.get("status") == "ok"
            result = ctx_data.get("result", {})
            assert "messages" in result
            assert isinstance(result["messages"], list)
            assert len(result["messages"]) == 0
            assert "stats" in result
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_session_context_with_messages(self, api_client):
        session_id = None
        try:
            resp = api_client.create_session()
            assert resp.status_code == 200
            session_id = resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "What is machine learning?")
            api_client.add_message(session_id, "assistant", "Machine learning is a subset of AI.")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            ctx_data = ctx_resp.json()
            assert ctx_data.get("status") == "ok"
            result = ctx_data.get("result", {})
            messages = result.get("messages", [])
            assert len(messages) >= 2

            roles = [m.get("role") for m in messages]
            assert "user" in roles
            assert "assistant" in roles

            stats = result.get("stats", {})
            assert "activeTokens" in stats
            assert stats["activeTokens"] >= 0
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_session_context_with_token_budget(self, api_client):
        session_id = None
        try:
            resp = api_client.create_session()
            assert resp.status_code == 200
            session_id = resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Test message for budget")

            ctx_resp = api_client.get_session_context(session_id, token_budget=1000)
            assert ctx_resp.status_code == 200
            assert ctx_resp.json().get("status") == "ok"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_context_with_zero_budget(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Budget test")

            ctx_resp = api_client.get_session_context(session_id, token_budget=0)
            assert ctx_resp.status_code == 200
            assert ctx_resp.json().get("status") == "ok"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_session_context_nonexistent(self, api_client):
        ctx_resp = api_client.get_session_context("nonexistent-session-xyz")
        assert ctx_resp.status_code == 200

    def test_session_context_contains_stats(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Stats test message")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            assert "stats" in result
            stats = result["stats"]
            assert "activeTokens" in stats
            assert isinstance(stats["activeTokens"], int)
            assert stats["activeTokens"] >= 0
            assert "messages" in result
            assert isinstance(result["messages"], list)
        finally:
            if session_id:
                api_client.delete_session(session_id)
