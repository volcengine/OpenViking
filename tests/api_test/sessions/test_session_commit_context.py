class TestSessionCommitContext:
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
        assert ctx_resp.status_code == 404
        body = ctx_resp.json()
        assert body.get("status") == "error"
        assert body.get("error", {}).get("code") == "NOT_FOUND"

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
