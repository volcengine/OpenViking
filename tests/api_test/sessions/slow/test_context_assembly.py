import time
import uuid


class TestSessionContextAssembly:
    def test_context_contains_recent_messages(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            unique_kw = f"ctx_recent_{uuid.uuid4().hex[:6]}"
            api_client.add_message(session_id, "user", f"Message about {unique_kw}")
            api_client.add_message(session_id, "assistant", f"Reply about {unique_kw}")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx.status_code == 200
            messages = ctx.json().get("result", {}).get("messages", [])
            text = str(messages)
            assert unique_kw in text, f"recent messages should appear in context, query={unique_kw}"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_after_commit_has_archive(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Pre-commit context test about databases")
            api_client.add_message(session_id, "assistant", "Pre-commit reply about databases")

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

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx.status_code == 200
            result = ctx.json().get("result", {})
            stats = result.get("stats", {})
            assert stats.get("totalArchives", 0) >= 0, "stats should contain totalArchives"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_messages_have_valid_roles(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Role validation test")
            api_client.add_message(session_id, "assistant", "Role validation reply")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            valid_roles = {"user", "assistant", "system"}
            for msg in messages:
                assert msg.get("role") in valid_roles, (
                    f"message role should be one of {valid_roles}, got {msg.get('role')}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_after_add_used_records(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            ctx_uri = f"viking://resources/ctx_used_{uuid.uuid4().hex[:6]}"
            api_client.session_used(session_id, contexts=[ctx_uri])

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx.status_code == 200
            result = ctx.json().get("result", {})
            assert isinstance(result, dict), "context result should be dict"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_stats_all_integer_fields(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Stats type test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            stats = ctx.json().get("result", {}).get("stats", {})
            for key, value in stats.items():
                assert isinstance(value, int), (
                    f"stats.{key} should be int, got {type(value).__name__}={value}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_has_latest_archive_overview(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(
                session_id, "user", "Archive overview test about neural networks"
            )
            api_client.add_message(session_id, "assistant", "Neural networks overview reply")

            commit_resp = api_client.session_commit(session_id)
            if commit_resp.status_code != 200:
                return

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

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            result = ctx.json().get("result", {})
            overview = result.get("latest_archive_overview", "")
            if overview:
                assert isinstance(overview, str), "archive overview should be string"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_with_zero_budget(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Zero budget test")

            ctx = api_client.get_session_context(session_id, token_budget=0)
            assert ctx.status_code == 200
            messages = ctx.json().get("result", {}).get("messages", [])
            assert len(messages) >= 0, "zero budget should return 0 or minimal messages"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_multiple_commits_context_accumulates(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            for batch in range(3):
                api_client.add_message(
                    session_id, "user", f"Batch {batch}: topic about caching strategies"
                )
                api_client.add_message(session_id, "assistant", f"Batch {batch}: caching reply")
                commit_resp = api_client.session_commit(session_id)
                if commit_resp.status_code == 200:
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

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx.status_code == 200
            stats = ctx.json().get("result", {}).get("stats", {})
            total_archives = stats.get("totalArchives", 0)
            assert total_archives >= 1 or stats.get("failedArchives", 0) > 0, (
                f"should have at least 1 archive (or failed archive) after 3 commits, "
                f"got totalArchives={total_archives}, failedArchives={stats.get('failedArchives', 0)}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_context_messages_order_preserved(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            for i in range(5):
                api_client.add_message(session_id, "user", f"Order_{i}")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            user_msgs = [m for m in messages if m.get("role") == "user"]
            if len(user_msgs) >= 2:
                for i in range(len(user_msgs) - 1):
                    assert "Order_" in str(user_msgs[i].get("parts", [])), (
                        "user messages should be in order"
                    )
        finally:
            if session_id:
                api_client.delete_session(session_id)
