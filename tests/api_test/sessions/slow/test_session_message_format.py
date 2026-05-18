import time
import uuid


class TestSessionMessageFormat:
    def test_message_parts_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Hello world")
            api_client.add_message(session_id, "assistant", "Hi there!")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])
            assert len(messages) >= 2

            for msg in messages:
                assert "role" in msg, f"message missing 'role', keys: {list(msg.keys())}"
                assert "parts" in msg, f"message missing 'parts', keys: {list(msg.keys())}"
                assert isinstance(msg["parts"], list), (
                    f"parts should be list, got {type(msg['parts'])}"
                )
                assert len(msg["parts"]) > 0, "parts should not be empty"

                for part in msg["parts"]:
                    assert "type" in part, f"part missing 'type', keys: {list(part.keys())}"
                    if part["type"] == "text":
                        assert "text" in part, (
                            f"text part missing 'text' field, keys: {list(part.keys())}"
                        )
                        assert isinstance(part["text"], str), (
                            f"part text should be str, got {type(part['text'])}"
                        )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_role_values(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "User message")
            api_client.add_message(session_id, "assistant", "Assistant message")

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])

            for msg in messages:
                assert msg["role"] in ["user", "assistant"], (
                    f"role should be 'user' or 'assistant', got '{msg['role']}'"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_content_preserved(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            unique_text = f"unique_content_{uuid.uuid4().hex[:8]}"
            api_client.add_message(session_id, "user", unique_text)

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])

            all_text = ""
            for msg in messages:
                for part in msg.get("parts", []):
                    if part.get("type") == "text":
                        all_text += part.get("text", "")

            assert unique_text in all_text, (
                f"added message content should appear in context, expected '{unique_text}' in '{all_text[:200]}'"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_order_preserved(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            markers = [f"marker_{i}_{uuid.uuid4().hex[:4]}" for i in range(5)]
            for i, marker in enumerate(markers):
                role = "user" if i % 2 == 0 else "assistant"
                api_client.add_message(session_id, role, marker)

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])

            positions = []
            for marker in markers:
                for idx, msg in enumerate(messages):
                    for part in msg.get("parts", []):
                        if part.get("type") == "text" and marker in part.get("text", ""):
                            positions.append(idx)
                            break

            for i in range(len(positions) - 1):
                assert positions[i] < positions[i + 1], (
                    f"message order should be preserved: marker {i} at pos {positions[i]}, marker {i + 1} at pos {positions[i + 1]}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_add_message_response_structure(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            msg_resp = api_client.add_message(session_id, "user", "Response structure test")
            assert msg_resp.status_code == 200
            data = msg_resp.json()
            assert data.get("status") == "ok"

            result = data.get("result", {})
            assert "message_count" in result, (
                f"add_message result should contain message_count, got keys: {sorted(result.keys())}"
            )
            assert isinstance(result["message_count"], int), (
                f"message_count should be int, got {type(result['message_count'])}"
            )
            assert result["message_count"] >= 1, (
                f"message_count should be >= 1 after adding, got {result['message_count']}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_with_special_characters(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            special_content = "Special chars: <>&\"'\\n\\t{json} [array] $variable @mention #tag"
            msg_resp = api_client.add_message(session_id, "user", special_content)
            assert msg_resp.status_code == 200, (
                f"message with special chars should be accepted, got {msg_resp.status_code}: {msg_resp.text[:200]}"
            )

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])
            all_text = ""
            for msg in messages:
                for part in msg.get("parts", []):
                    if part.get("type") == "text":
                        all_text += part.get("text", "")

            assert "Special chars" in all_text, (
                "special character content should be preserved in context"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_with_unicode(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            unicode_content = "中文消息 🎉 日本語テスト 한국어 테스트"
            msg_resp = api_client.add_message(session_id, "user", unicode_content)
            assert msg_resp.status_code == 200

            ctx_resp = api_client.get_session_context(session_id)
            assert ctx_resp.status_code == 200
            messages = ctx_resp.json().get("result", {}).get("messages", [])
            all_text = ""
            for msg in messages:
                for part in msg.get("parts", []):
                    if part.get("type") == "text":
                        all_text += part.get("text", "")

            assert "中文消息" in all_text, "Unicode content should be preserved"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_with_very_long_content(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            long_content = "A" * 50000
            msg_resp = api_client.add_message(session_id, "user", long_content)
            assert msg_resp.status_code == 200, (
                f"long message should return 200 or 413 (too large), got {msg_resp.status_code}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_contexts_format(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Used format test")

            test_contexts = [
                "viking://resources/ctx_a",
                "viking://resources/ctx_b",
            ]
            used_resp = api_client.session_used(
                session_id,
                contexts=test_contexts,
            )
            assert used_resp.status_code == 200
            result = used_resp.json().get("result", {})

            contexts_used = result.get("contexts_used", [])
            assert isinstance(contexts_used, (list, int)), (
                f"contexts_used should be list or int, got {type(contexts_used)}"
            )
            for ctx in contexts_used if isinstance(contexts_used, list) else []:
                assert isinstance(ctx, str), f"each context should be str, got {type(ctx)}: {ctx}"
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_session_used_skills_format(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Skill format test")

            used_resp = api_client.session_used(
                session_id,
                skill={"name": "test-skill", "uri": "viking://agent/skills/test"},
            )
            assert used_resp.status_code == 200
            result = used_resp.json().get("result", {})

            skills_used = result.get("skills_used", [])
            assert isinstance(skills_used, (list, int)), (
                f"skills_used should be list or int, got {type(skills_used)}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_commit_result_archived_field(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(session_id, "user", "Archived field test")
            api_client.add_message(session_id, "assistant", "Archived field test response")

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200
            result = commit_resp.json().get("result", {})

            assert "archived" in result, (
                f"commit result should contain 'archived', got keys: {sorted(result.keys())}"
            )
            assert isinstance(result["archived"], bool), (
                f"archived should be bool, got {type(result['archived'])}"
            )

            if "archive_uri" in result:
                assert isinstance(result["archive_uri"], str), "archive_uri should be str"
                assert result["archive_uri"].startswith("viking://"), (
                    f"archive_uri format invalid: {result['archive_uri']}"
                )
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_pending_tokens_reset_after_commit(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(
                session_id, "user", "Pending tokens test message with enough content"
            )
            api_client.add_message(session_id, "assistant", "Pending tokens test response")

            get_before = api_client.get_session(session_id)
            pending_before = get_before.json().get("result", {}).get("pending_tokens", 0)
            assert pending_before > 0, (
                f"pending_tokens should be > 0 before commit, got {pending_before}"
            )

            commit_resp = api_client.session_commit(session_id)
            assert commit_resp.status_code == 200

            time.sleep(2)

            get_after = api_client.get_session(session_id)
            pending_after = get_after.json().get("result", {}).get("pending_tokens", -1)

            assert pending_after < pending_before, (
                f"pending_tokens should decrease after commit, before={pending_before} after={pending_after}"
            )
        finally:
            if session_id:
                api_client.delete_session(session_id)
