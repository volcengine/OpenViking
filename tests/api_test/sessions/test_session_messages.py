import uuid
from math import ceil


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

    def test_cjk_message_uses_cjk_aware_token_estimate(self, api_client):
        cjk_session_id = None
        ascii_session_id = None
        try:
            cjk_text = "你好世界" * 100
            ascii_text = "abcd" * 100
            cjk_expected_min = ceil(len(cjk_text) * 1.5)
            naive_chars_div_4 = ceil(len(cjk_text) / 4)

            cjk_resp = api_client.create_session()
            assert cjk_resp.status_code == 200
            cjk_session_id = cjk_resp.json()["result"]["session_id"]
            add_cjk = api_client.add_message(cjk_session_id, "user", cjk_text)
            assert add_cjk.status_code == 200

            cjk_detail = api_client.get_session(cjk_session_id)
            assert cjk_detail.status_code == 200
            cjk_pending = cjk_detail.json().get("result", {}).get("pending_tokens", 0)
            assert cjk_pending >= cjk_expected_min, (
                "pending_tokens should use the CJK-aware estimate; "
                f"got {cjk_pending}, expected at least {cjk_expected_min}, "
                f"old chars/4 estimate would be {naive_chars_div_4}"
            )

            cjk_ctx = api_client.get_session_context(cjk_session_id, token_budget=128000)
            assert cjk_ctx.status_code == 200
            cjk_result = cjk_ctx.json().get("result", {})
            cjk_estimated = cjk_result.get("estimatedTokens", 0)
            cjk_active = cjk_result.get("stats", {}).get("activeTokens", 0)
            assert cjk_estimated >= cjk_expected_min, (
                f"context estimatedTokens should preserve CJK-aware estimate, got {cjk_estimated}"
            )
            assert cjk_active >= cjk_expected_min, (
                f"context stats.activeTokens should preserve CJK-aware estimate, got {cjk_active}"
            )

            ascii_resp = api_client.create_session()
            assert ascii_resp.status_code == 200
            ascii_session_id = ascii_resp.json()["result"]["session_id"]
            add_ascii = api_client.add_message(ascii_session_id, "user", ascii_text)
            assert add_ascii.status_code == 200

            ascii_detail = api_client.get_session(ascii_session_id)
            assert ascii_detail.status_code == 200
            ascii_pending = ascii_detail.json().get("result", {}).get("pending_tokens", 0)
            assert cjk_pending >= ascii_pending * 4, (
                "same-length CJK should be estimated much higher than ASCII; "
                f"cjk={cjk_pending}, ascii={ascii_pending}"
            )
        finally:
            if cjk_session_id:
                api_client.delete_session(cjk_session_id)
            if ascii_session_id:
                api_client.delete_session(ascii_session_id)

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

    def test_message_peer_id_field(self, api_client):
        session_id = None
        try:
            r = api_client.create_session()
            session_id = r.json()["result"]["session_id"]
            api_client.add_message(session_id, "user", "Peer ID test", peer_id="web_user_1")

            ctx = api_client.get_session_context(session_id, token_budget=128000)
            messages = ctx.json().get("result", {}).get("messages", [])
            for msg in messages:
                if "peer_id" in msg:
                    assert isinstance(msg["peer_id"], str)
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
