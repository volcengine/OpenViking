import json

import pytest
import requests


class TestSessionUsedCommit:
    def test_session_used_commit(self, api_client):
        session_id = None

        try:
            response = api_client.create_session()
            assert response.status_code == 200, "Create session failed"
            data = response.json()
            session_id = data["result"]["session_id"]
            assert session_id is not None, "session_id should not be null"

            response = api_client.add_message(session_id, "user", "Hello, test message!")
            assert response.status_code == 200, "Add message failed"
            data = response.json()
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

            response = api_client.session_used(session_id)
            assert response.status_code == 200, "Session used failed"
            data = response.json()
            print("\n" + "=" * 80)
            print("Session Used API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

            response = api_client.session_commit(session_id)
            assert response.status_code == 200, "Session commit failed"
            data = response.json()
            print("\n" + "=" * 80)
            print("Session Commit API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

            response = api_client.delete_session(session_id)
            assert response.status_code == 200, "Delete session failed"

        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")
