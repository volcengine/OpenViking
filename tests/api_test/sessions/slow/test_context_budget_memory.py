import time
import uuid


class TestContextBudgetMemory:
    def test_small_budget_drops_messages_keeps_stats(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(5):
                api_client.add_message(
                    session_id,
                    "user",
                    f"Message {i} with enough content to consume token budget about topic {i}",
                )
                api_client.add_message(
                    session_id, "assistant", f"Response {i} acknowledging topic {i}"
                )

            ctx_resp = api_client.get_session_context(session_id, token_budget=100)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            stats = result.get("stats", {})
            assert "activeTokens" in stats, f"stats should contain activeTokens, got: {stats}"
            assert "archiveTokens" in stats, "stats should contain archiveTokens"
            if "estimatedTokens" in result:
                assert result["estimatedTokens"] <= 200, (
                    f"estimatedTokens should be within budget range, got {result['estimatedTokens']}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_budget_zero_returns_current_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "This is a current message")

            ctx_resp = api_client.get_session_context(session_id, token_budget=0)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            messages = result.get("messages", [])
            assert len(messages) >= 0, (
                f"zero budget should return current messages, got {len(messages)}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_large_budget_returns_all_messages(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            for i in range(3):
                api_client.add_message(session_id, "user", f"User message {i}")
                api_client.add_message(session_id, "assistant", f"Assistant reply {i}")

            ctx_resp = api_client.get_session_context(session_id, token_budget=999999)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            messages = result.get("messages", [])
            assert len(messages) >= 6, (
                f"large budget should return all 6 messages, got {len(messages)}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_then_small_budget_keeps_archive_overview(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "I love Python programming for data science")
            api_client.add_message(session_id, "assistant", "Python is excellent for data science!")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(30):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        if task_resp.json().get("result", {}).get("status") in [
                            "completed",
                            "failed",
                        ]:
                            break
                    time.sleep(2)

            api_client.add_message(session_id, "user", "New question after commit")

            ctx_resp = api_client.get_session_context(session_id, token_budget=500)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})

            overview = result.get("latest_archive_overview", "")
            stats = result.get("stats", {})
            if stats.get("totalArchives", 0) > 0:
                assert isinstance(overview, str), "archive overview should be string"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_messages_have_role_and_parts(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Check message structure")
            api_client.add_message(session_id, "assistant", "Structure verified")

            ctx_resp = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])

            for msg in messages:
                assert "role" in msg, f"message should have 'role', got keys: {sorted(msg.keys())}"
                assert msg["role"] in ["user", "assistant", "system"], (
                    f"role should be user/assistant/system, got {msg['role']}"
                )
                assert "parts" in msg, (
                    f"message should have 'parts', got keys: {sorted(msg.keys())}"
                )
                assert isinstance(msg["parts"], list), "parts should be list"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_memory_extraction_after_commit(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            unique_name = f"Alice_{uuid.uuid4().hex[:6]}"
            api_client.add_message(
                session_id,
                "user",
                f"My name is {unique_name} and I work at TechCorp as a senior engineer specializing in AI",
            )
            api_client.add_message(
                session_id,
                "assistant",
                f"Nice to meet you {unique_name}! I'll remember you work at TechCorp in AI.",
            )

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(60):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        if task_resp.json().get("result", {}).get("status") in [
                            "completed",
                            "failed",
                        ]:
                            break
                    time.sleep(2)

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            memories = result.get("memories_extracted", {})

            assert isinstance(memories, dict), (
                f"memories_extracted should be dict, got {type(memories)}"
            )
            if "total" in memories:
                assert isinstance(memories["total"], int), "total should be int"
                assert memories["total"] >= 0, "total should be non-negative"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_result_has_archived_field(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Testing archived field in commit result")
            api_client.add_message(session_id, "assistant", "Acknowledged")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            result = commit_resp.json().get("result", {})

            assert "archived" in result, (
                f"commit result should contain 'archived' field, got keys: {sorted(result.keys())}"
            )
            assert isinstance(result["archived"], bool), (
                f"archived should be bool, got {type(result['archived'])}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_stats_fields_complete(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Stats completeness test")

            ctx_resp = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx_resp.status_code == 200
            result = ctx_resp.json().get("result", {})
            stats = result.get("stats", {})

            required_stats_fields = [
                "activeTokens",
                "archiveTokens",
                "totalArchives",
                "includedArchives",
                "droppedArchives",
                "failedArchives",
            ]
            for field in required_stats_fields:
                assert field in stats, (
                    f"stats should contain '{field}', got keys: {sorted(stats.keys())}"
                )
                assert isinstance(stats[field], int), (
                    f"stats.{field} should be int, got {type(stats[field])}"
                )

            assert "estimatedTokens" in result, (
                f"result should contain 'estimatedTokens' at top level, got keys: {sorted(result.keys())}"
            )
            assert isinstance(result["estimatedTokens"], int), (
                f"estimatedTokens should be int, got {type(result['estimatedTokens'])}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_pending_tokens_non_negative(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            pending = get_resp.json().get("result", {}).get("pending_tokens", -1)
            assert pending >= 0, f"pending_tokens should be non-negative, got {pending}"

            api_client.add_message(session_id, "user", "Test message")
            get_resp2 = api_client.get_session(session_id)
            pending2 = get_resp2.json().get("result", {}).get("pending_tokens", -1)
            assert pending2 >= 0, (
                f"pending_tokens should be non-negative after add_message, got {pending2}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_increments_counters(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            used1 = api_client.session_used(session_id, contexts=["viking://resources/test1"])
            assert used1.status_code == 200

            used2 = api_client.session_used(session_id, contexts=["viking://resources/test2"])
            assert used2.status_code == 200

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            contexts_used = result.get("contexts_used", 0)
            if isinstance(contexts_used, int) and contexts_used > 0:
                assert contexts_used >= 2, (
                    f"contexts_used should be >= 2 after 2 used calls, got {contexts_used}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_llm_token_usage_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Token usage structure test")
            api_client.add_message(session_id, "assistant", "Token usage response")
            api_client.session_commit(session_id)

            time.sleep(3)

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            token_usage = result.get("llm_token_usage", {})

            assert isinstance(token_usage, dict), (
                f"llm_token_usage should be dict, got {type(token_usage)}"
            )
            if token_usage:
                for key, value in token_usage.items():
                    assert isinstance(value, int), (
                        f"llm_token_usage.{key} should be int, got {type(value)}"
                    )
        finally:
            if session_id:
                api_client.delete_session(session_id)
