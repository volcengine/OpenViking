import uuid


class TestSessionMessages:
    def test_add_message_increments_count(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            msg1 = api_client.add_message(session_id, "user", "First message")
            assert msg1.status_code == 200
            assert msg1.json()["result"]["message_count"] == 1

            msg2 = api_client.add_message(session_id, "assistant", "Second message")
            assert msg2.status_code == 200
            assert msg2.json()["result"]["message_count"] == 2
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_auto_create_on_add_message(self, api_client):
        random_id = f"auto-create-{uuid.uuid4().hex[:8]}"
        try:
            msg_resp = api_client.add_message(random_id, "user", "Auto-create test")
            assert msg_resp.status_code == 200
            data = msg_resp.json()
            assert data.get("status") == "ok"
            assert data["result"]["session_id"] == random_id
            assert data["result"]["message_count"] == 1
        finally:
            try:
                api_client.delete_session(random_id)
            except Exception:
                pass

    def test_add_message_with_parts(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/sessions/{session_id}/messages",
                json={
                    "role": "assistant",
                    "parts": [
                        {"type": "text", "text": "Here is the answer"},
                        {
                            "type": "context",
                            "uri": "viking://resources/test",
                            "context_type": "resource",
                            "abstract": "A test resource",
                        },
                    ],
                },
            )
            assert resp.status_code == 200
            assert resp.json().get("status") == "ok"
            assert resp.json()["result"]["message_count"] >= 1
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_add_message_without_content_or_parts_returns_400(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/sessions/{session_id}/messages",
                json={"role": "user"},
            )
            assert resp.status_code == 400
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_add_message_with_metadata(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            resp = api_client._request_with_retry(
                "POST",
                f"{api_client.server_url}/api/v1/sessions/{session_id}/messages",
                json={
                    "role": "user",
                    "content": "Message with metadata",
                    "metadata": {"source": "api_test", "version": "1.0"},
                },
            )
            assert resp.status_code == 200
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_empty_content_message(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "")

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            count = get_resp.json().get("result", {}).get("message_count", 0)
            assert count >= 1
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_system_role_message(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "system", "System instruction for the session")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            roles = [m.get("role") for m in messages]
            assert "system" in roles
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_special_chars_in_content(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            special = '<script>alert("xss")</script> & "quotes" \'single\' {json: true}'
            api_client.add_message(session_id, "user", special)

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            found = any("script" in str(m.get("parts", [])) for m in messages)
            assert found
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_unicode_content_preserved(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            unicode_text = "你好世界 🌍 こんにちは 한국어 café résumé"
            api_client.add_message(session_id, "user", unicode_text)

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            found = False
            for msg in messages:
                for part in msg.get("parts", []):
                    if "text" in part and "你好" in part["text"]:
                        found = True
                        assert "🌍" in part["text"]
                        assert "café" in part["text"]
            assert found
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_multiline_content_preserved(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            multiline = "Line 1\nLine 2\nLine 3\n\nParagraph 2"
            api_client.add_message(session_id, "user", multiline)

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            found = False
            for msg in messages:
                for part in msg.get("parts", []):
                    if "text" in part and "Line 1" in part["text"]:
                        found = True
                        assert "Line 2" in part["text"]
            assert found
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_long_content_not_truncated_in_context(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            long_content = "X" * 5000
            api_client.add_message(session_id, "user", long_content)

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            found = any(
                any("text" in part and len(part["text"]) > 4000 for part in msg.get("parts", []))
                for msg in messages
            )
            assert found
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_order_preserved(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]

            for i in range(5):
                api_client.add_message(session_id, "user", f"Order test message {i}")
                api_client.add_message(session_id, "assistant", f"Order test reply {i}")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])

            user_indices = []
            for idx, msg in enumerate(messages):
                if msg.get("role") == "user":
                    text = str(msg.get("parts", []))
                    for i in range(5):
                        if f"Order test message {i}" in text:
                            user_indices.append((i, idx))

            if len(user_indices) >= 2:
                for j in range(len(user_indices) - 1):
                    assert user_indices[j][1] < user_indices[j + 1][1]
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_parts_is_list_of_dicts(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Parts structure test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            assert ctx.status_code == 200
            messages = ctx.json().get("result", {}).get("messages", [])
            assert len(messages) > 0

            msg = messages[0]
            parts = msg.get("parts", [])
            assert isinstance(parts, list)
            for p in parts:
                assert isinstance(p, dict)
                assert "type" in p
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_part_type_text(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Text type part test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            for msg in messages:
                for part in msg.get("parts", []):
                    if "text" in part:
                        assert part.get("type") == "text"
                        assert isinstance(part["text"], str)
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_has_id_field(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "ID field test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            for msg in messages:
                assert "id" in msg
                assert isinstance(msg["id"], str)
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_has_created_at(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Timestamp test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            for msg in messages:
                assert "created_at" in msg
                assert isinstance(msg["created_at"], str)
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_message_role_id_field(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Role ID test")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            for msg in messages:
                if "role_id" in msg:
                    assert isinstance(msg["role_id"], str)
        finally:
            if session_id:
                api_client.delete_session(session_id)

    def test_get_session_contains_pending_tokens(self, api_client):
        session_id = None
        try:
            create_resp = api_client.create_session()
            assert create_resp.status_code == 200
            session_id = create_resp.json()["result"]["session_id"]

            api_client.add_message(
                session_id, "user", "This is a test message for pending tokens calculation."
            )

            get_resp = api_client.get_session(session_id)
            assert get_resp.status_code == 200
            result = get_resp.json().get("result", {})
            assert "pending_tokens" in result
            assert isinstance(result["pending_tokens"], int)
            assert result["pending_tokens"] > 0
        finally:
            if session_id:
                api_client.delete_session(session_id)
