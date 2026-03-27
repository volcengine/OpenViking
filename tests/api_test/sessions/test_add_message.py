import json


class TestAddMessage:
    def test_add_message(self, api_client):
        session_id = None

        try:
            response = api_client.create_session()
            assert response.status_code == 200, "Create session failed"
            data = response.json()
            session_id = data["result"]["session_id"]
            assert session_id is not None, "session_id should not be null"

            response = api_client.add_message(session_id, "user", "Hello, how can I help?")
            assert response.status_code == 200, "Add message failed"
            data = response.json()
            print("\n" + "=" * 80)
            print("Add Message API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.delete_session(session_id)
            assert response.status_code == 200, "Delete session failed"

        except Exception as e:
            print(f"Error: {e}")
            raise
