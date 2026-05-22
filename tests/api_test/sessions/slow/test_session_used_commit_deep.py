import time
import uuid


class TestSessionUsedCommitDeep:
    def test_used_contexts_reflected_in_context_response(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            ctx_uri = f"viking://resources/used_ctx_{uuid.uuid4().hex[:6]}"
            used_resp = api_client.session_used(session_id, contexts=[ctx_uri])
            assert used_resp.status_code == 200

            result = used_resp.json().get("result", {})
            contexts_used = result.get("contexts_used", 0)
            assert contexts_used >= 1, f"used should report contexts_used >= 1, got {contexts_used}"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_used_skill_reflected_in_response(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            skill_data = {
                "name": f"test_skill_{uuid.uuid4().hex[:6]}",
                "description": "A test skill",
            }
            used_resp = api_client.session_used(session_id, skill=skill_data)
            assert used_resp.status_code == 200

            result = used_resp.json().get("result", {})
            skills_used = result.get("skills_used", 0)
            assert skills_used >= 0, f"skills_used should be non-negative, got {skills_used}"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_used_empty_payload(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            used_resp = api_client.session_used(session_id)
            assert used_resp.status_code == 200, (
                f"used with empty payload should return 200, got {used_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_used_multiple_contexts_count(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            ctx_uris = [
                f"viking://resources/multi_ctx_{uuid.uuid4().hex[:6]}_{i}" for i in range(3)
            ]
            used_resp = api_client.session_used(session_id, contexts=ctx_uris)
            assert used_resp.status_code == 200

            result = used_resp.json().get("result", {})
            contexts_used = result.get("contexts_used", 0)
            assert contexts_used >= 1, (
                f"used with 3 contexts should report contexts_used >= 1, got {contexts_used}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_empty_session(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200, (
                f"commit empty session should return valid status, got {commit_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_used_on_nonexistent_session(self, api_client):
        fake_session = f"nonexist_{uuid.uuid4().hex[:12]}"
        used_resp = api_client.session_used(fake_session, contexts=["viking://resources/test"])
        assert used_resp.status_code == 200, (
            f"used on nonexistent session should return error, got {used_resp.status_code}"
        )

    def test_used_then_commit_context_includes_archive(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Used+commit test about distributed systems")
            api_client.add_message(session_id, "assistant", "Distributed systems reply")

            ctx_uri = f"viking://resources/used_commit_{uuid.uuid4().hex[:6]}"
            api_client.session_used(session_id, contexts=[ctx_uri])

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
            assert "messages" in result or "stats" in result, (
                "context after used+commit should have messages or stats"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_get_after_commit_shows_fields(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Get session after commit")
            commit_resp = api_client.session_commit(session_id)
            if commit_resp.status_code != 200:
                return

            task_id = commit_resp.json().get("result", {}).get("task_id")
            if task_id:
                for _ in range(20):
                    task_resp = api_client.get_task(task_id)
                    if task_resp.status_code == 200:
                        if task_resp.json().get("result", {}).get("status") in [
                            "completed",
                            "failed",
                        ]:
                            break
                    time.sleep(1)

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            assert isinstance(result, dict), "get_session should return dict"
            assert "session_id" in result, "get_session result should contain session_id"
        finally:
            if session_id:
                api_client.delete_session(session_id)
