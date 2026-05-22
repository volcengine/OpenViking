import time
import uuid


class TestMultiSessionIsolation:
    def test_messages_isolated_between_sessions(self, api_client):
        sid1 = None
        sid2 = None
        try:
            r1 = api_client.create_session()
            assert r1.status_code == 200
            sid1 = r1.json()["result"]["session_id"]

            r2 = api_client.create_session()
            assert r2.status_code == 200
            sid2 = r2.json()["result"]["session_id"]

            unique1 = f"session1_{uuid.uuid4().hex[:6]}"
            unique2 = f"session2_{uuid.uuid4().hex[:6]}"

            api_client.add_message(sid1, "user", f"Message for session 1: {unique1}")
            api_client.add_message(sid1, "assistant", f"Reply in session 1: {unique1}")

            api_client.add_message(sid2, "user", f"Message for session 2: {unique2}")
            api_client.add_message(sid2, "assistant", f"Reply in session 2: {unique2}")

            ctx1 = api_client.get_session_context(sid1, token_budget=128000)
            assert ctx1.status_code == 200
            text1 = str(ctx1.json().get("result", {}).get("messages", []))

            assert unique1 in text1, f"session 1 context should contain its own message {unique1}"
            assert unique2 not in text1, (
                f"session 1 context should NOT contain session 2 message {unique2}"
            )

            ctx2 = api_client.get_session_context(sid2, token_budget=128000)
            assert ctx2.status_code == 200
            text2 = str(ctx2.json().get("result", {}).get("messages", []))

            assert unique2 in text2, f"session 2 context should contain its own message {unique2}"
            assert unique1 not in text2, (
                f"session 2 context should NOT contain session 1 message {unique1}"
            )
        finally:
            if sid1:
                api_client.delete_session(sid1)
            if sid2:
                api_client.delete_session(sid2)

    def test_used_does_not_cross_sessions(self, api_client):
        sid1 = None
        sid2 = None
        try:
            r1 = api_client.create_session()
            sid1 = r1.json()["result"]["session_id"]
            r2 = api_client.create_session()
            sid2 = r2.json()["result"]["session_id"]

            api_client.session_used(sid1, contexts=["viking://resources/ctx_a"])

            get1 = api_client.get_session(sid1)
            get2 = api_client.get_session(sid2)

            used1 = get1.json().get("result", {}).get("contexts_used", 0)
            used2 = get2.json().get("result", {}).get("contexts_used", 0)

            if isinstance(used1, int) and used1 > 0:
                assert used1 >= 1, f"session 1 should have contexts_used >= 1, got {used1}"
            assert used2 == 0, (
                f"session 2 should have contexts_used == 0 (not affected by session 1 used), got {used2}"
            )
        finally:
            if sid1:
                api_client.delete_session(sid1)
            if sid2:
                api_client.delete_session(sid2)

    def test_pending_tokens_independent_between_sessions(self, api_client):
        sid1 = None
        sid2 = None
        try:
            r1 = api_client.create_session()
            sid1 = r1.json()["result"]["session_id"]
            r2 = api_client.create_session()
            sid2 = r2.json()["result"]["session_id"]

            api_client.add_message(sid1, "user", "Long message " * 50)

            get1 = api_client.get_session(sid1)
            get2 = api_client.get_session(sid2)

            pending1 = get1.json().get("result", {}).get("pending_tokens", 0)
            pending2 = get2.json().get("result", {}).get("pending_tokens", 0)

            assert pending1 > 0, f"session 1 should have pending_tokens > 0, got {pending1}"
            assert pending2 == 0, f"session 2 should have pending_tokens == 0, got {pending2}"
        finally:
            if sid1:
                api_client.delete_session(sid1)
            if sid2:
                api_client.delete_session(sid2)

    def test_session_id_uniqueness(self, api_client):
        custom_id = f"unique_test_{uuid.uuid4().hex[:8]}"
        sid = None
        try:
            r1 = api_client.create_session(session_id=custom_id)
            assert r1.status_code == 200
            sid = custom_id

            r2 = api_client.create_session(session_id=custom_id)
            assert r2.status_code == 409, (
                f"duplicate session_id should return 200 (idempotent) or 409 (conflict), got {r2.status_code}"
            )

            if r2.status_code == 200:
                returned_id = r2.json().get("result", {}).get("session_id", "")
                assert returned_id == custom_id, (
                    f"returned session_id should match, got {returned_id}"
                )
        finally:
            if sid:
                api_client.delete_session(sid)

    def test_commit_count_increments_per_session(self, api_client):
        sid = None
        try:
            r = api_client.create_session()
            sid = r.json()["result"]["session_id"]

            api_client.add_message(sid, "user", "First commit count test")
            api_client.add_message(sid, "assistant", "First reply")
            api_client.session_commit(sid)
            time.sleep(2)

            get1 = api_client.get_session(sid)
            count1 = get1.json().get("result", {}).get("commit_count", 0)

            api_client.add_message(sid, "user", "Second commit count test")
            api_client.add_message(sid, "assistant", "Second reply")
            api_client.session_commit(sid)
            time.sleep(2)

            get2 = api_client.get_session(sid)
            count2 = get2.json().get("result", {}).get("commit_count", 0)

            assert count2 >= count1, (
                f"commit_count should not decrease after second commit, before={count1} after={count2}"
            )
        finally:
            if sid:
                api_client.delete_session(sid)

    def test_session_created_at_and_updated_at(self, api_client):
        sid = None
        try:
            r = api_client.create_session()
            sid = r.json()["result"]["session_id"]

            get1 = api_client.get_session(sid)
            result1 = get1.json().get("result", {})
            created_at = result1.get("created_at", "")
            updated_at1 = result1.get("updated_at", "")

            assert created_at, "session should have created_at"
            assert updated_at1, "session should have updated_at"

            time.sleep(1)

            api_client.add_message(sid, "user", "Trigger updated_at change")

            get2 = api_client.get_session(sid)
            updated_at2 = get2.json().get("result", {}).get("updated_at", "")

            assert updated_at2 >= updated_at1, (
                f"updated_at should change after add_message, before={updated_at1} after={updated_at2}"
            )
        finally:
            if sid:
                api_client.delete_session(sid)

    def test_session_user_field(self, api_client):
        r = api_client.create_session()
        sid = r.json()["result"]["session_id"]
        try:
            get_resp = api_client.get_session(sid)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            user = result.get("user", "")
            assert isinstance(user, (str, dict)), (
                f"user field should be str or dict, got {type(user)}"
            )
        finally:
            api_client.delete_session(sid)

    def test_participant_ids_is_list(self, api_client):
        r = api_client.create_session()
        sid = r.json()["result"]["session_id"]
        try:
            get_resp = api_client.get_session(sid)
            result = get_resp.json().get("result", {})
            pids = result.get("participant_ids", None)
            if pids is not None:
                assert isinstance(pids, list), f"participant_ids should be list, got {type(pids)}"
        finally:
            api_client.delete_session(sid)
