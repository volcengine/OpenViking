import time


class TestSessionContextBudget:
    def test_context_default_budget_returns_all_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(5):
                api_client.add_message(session_id, "user", f"Message {i} for budget test")

            ctx_resp = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx_resp.status_code == 200, (
                f"context should return 200, got {ctx_resp.status_code}: {ctx_resp.text[:200]}"
            )
            result = ctx_resp.json().get("result", {})

            messages = result.get("messages", [])
            assert len(messages) >= 5, (
                f"with large budget all messages should be included, got {len(messages)}"
            )

            stats = result.get("stats", {})
            assert "activeTokens" in stats, "stats should contain activeTokens"
            assert "archiveTokens" in stats, "stats should contain archiveTokens"
            assert "totalArchives" in stats, "stats should contain totalArchives"
            assert "includedArchives" in stats, "stats should contain includedArchives"
            assert "droppedArchives" in stats, "stats should contain droppedArchives"
            assert "failedArchives" in stats, "stats should contain failedArchives"
            assert stats["activeTokens"] > 0, "activeTokens should be positive with messages"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_small_budget_drops_archives(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(3):
                api_client.add_message(session_id, "user", f"Archive budget msg {i}")
                api_client.add_message(session_id, "assistant", f"Archive budget resp {i}")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        task_data = task_resp.json().get("result", {})
                        if task_data.get("status") in ["completed", "failed"]:
                            break
                    time.sleep(2)

            api_client.add_message(session_id, "user", "Post-commit message for budget")

            ctx_resp = api_client.get_session_context(session_id, token_budget=1)
            assert ctx_resp.status_code == 200, (
                f"context with small budget should return 200, got {ctx_resp.status_code}"
            )
            result = ctx_resp.json().get("result", {})

            stats = result.get("stats", {})
            assert "droppedArchives" in stats
            assert "includedArchives" in stats
            assert stats.get("includedArchives", -1) == 0, (
                f"with budget=1 no archives should be included, got includedArchives={stats.get('includedArchives')}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_stats_active_tokens_matches_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            ctx_before = api_client.get_session_context(session_id)
            assert ctx_before.status_code == 200
            active_before = (
                ctx_before.json().get("result", {}).get("stats", {}).get("activeTokens", 0)
            )

            api_client.add_message(session_id, "user", "Token counting test message one")
            api_client.add_message(session_id, "assistant", "Token counting test response one")

            ctx_after = api_client.get_session_context(session_id)
            assert ctx_after.status_code == 200
            active_after = (
                ctx_after.json().get("result", {}).get("stats", {}).get("activeTokens", 0)
            )

            assert active_after > active_before, (
                f"activeTokens should increase after adding messages, before={active_before} after={active_after}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_archive_tokens_included_in_estimated(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(4):
                api_client.add_message(
                    session_id,
                    "user",
                    f"Archive token msg {i} with enough content to generate overview",
                )
                api_client.add_message(
                    session_id,
                    "assistant",
                    f"Archive token resp {i} with detailed information about the topic",
                )

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        task_data = task_resp.json().get("result", {})
                        if task_data.get("status") in ["completed", "failed"]:
                            break
                    time.sleep(2)

            api_client.add_message(session_id, "user", "New message after archive")

            ctx_resp = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            stats = result.get("stats", {})

            estimated_tokens = result.get("estimatedTokens", 0)
            active_tokens = stats.get("activeTokens", 0)
            archive_tokens = stats.get("archiveTokens", 0)

            assert estimated_tokens == active_tokens + archive_tokens, (
                f"estimatedTokens ({estimated_tokens}) should equal activeTokens ({active_tokens}) + archiveTokens ({archive_tokens})"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_messages_have_required_fields(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Field validation message")
            api_client.add_message(session_id, "assistant", "Field validation response")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])
            assert len(messages) >= 2, f"should have at least 2 messages, got {len(messages)}"

            for msg in messages:
                assert "role" in msg, f"message should contain 'role', got keys: {list(msg.keys())}"
                assert msg["role"] in ["user", "assistant"], (
                    f"role should be user/assistant, got {msg['role']}"
                )
                assert "parts" in msg, (
                    f"message should contain 'parts', got keys: {list(msg.keys())}"
                )
                assert isinstance(msg["parts"], list), (
                    f"parts should be a list, got {type(msg['parts'])}"
                )
                for part in msg["parts"]:
                    assert "type" in part, (
                        f"part should contain 'type', got keys: {list(part.keys())}"
                    )
                    if part.get("type") == "text":
                        assert "text" in part, "text part should contain 'text' field"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_latest_archive_overview_field(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            assert "latest_archive_overview" in result, (
                "context should contain latest_archive_overview field"
            )
            assert "pre_archive_abstracts" in result, (
                "context should contain pre_archive_abstracts field (backward compat)"
            )
            assert isinstance(result["pre_archive_abstracts"], list), (
                "pre_archive_abstracts should be a list"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_after_commit_includes_archive_overview(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(3):
                api_client.add_message(
                    session_id,
                    "user",
                    f"Archive overview test message {i} about Python programming",
                )
                api_client.add_message(
                    session_id,
                    "assistant",
                    f"Archive overview test response {i} about Python features",
                )

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        task_data = task_resp.json().get("result", {})
                        if task_data.get("status") in ["completed", "failed"]:
                            break
                    time.sleep(2)

            api_client.add_message(session_id, "user", "Post-commit query")

            ctx_resp = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            overview = result.get("latest_archive_overview", "")
            stats = result.get("stats", {})

            if stats.get("totalArchives", 0) > 0 and stats.get("includedArchives", 0) > 0:
                assert len(overview) > 0, (
                    "latest_archive_overview should not be empty when archives are included"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_estimated_tokens_non_negative(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Token non-negative test")

            for budget in [1, 100, 1000, 128000]:
                ctx_resp = api_client.get_session_context(session_id, token_budget=budget)
                assert ctx_resp.status_code == 200, f"budget={budget} should return 200"
                result = ctx_resp.json().get("result", {})
                estimated = result.get("estimatedTokens", -1)
                assert estimated >= 0, (
                    f"estimatedTokens should be non-negative, got {estimated} with budget={budget}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_empty_session_returns_empty_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            messages = result.get("messages", [])
            assert isinstance(messages, list), "messages should be a list"
            assert len(messages) == 0, f"empty session should have 0 messages, got {len(messages)}"

            stats = result.get("stats", {})
            assert stats.get("activeTokens", -1) == 0, "empty session should have 0 activeTokens"
            assert stats.get("archiveTokens", -1) == 0, "empty session should have 0 archiveTokens"
            assert stats.get("totalArchives", -1) == 0, "empty session should have 0 totalArchives"
        finally:
            if session_id:
                api_client.delete_session(session_id)
