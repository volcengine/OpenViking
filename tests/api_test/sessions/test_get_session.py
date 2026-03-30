import json

import pytest
import requests


class TestGetSession:
    def test_get_session(self, api_client):
        session_id = None

        try:
            response = api_client.create_session()
            assert response.status_code == 200, "Create session failed"
            data = response.json()
            session_id = data["result"]["session_id"]
            assert session_id is not None, "session_id should not be null"

            response = api_client.get_session(session_id)
            assert response.status_code == 200, "Get session failed"
            data = response.json()
            print("\n" + "=" * 80)
            print("Get Session API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"]["session_id"] == session_id, "session_id mismatch"

            response = api_client.delete_session(session_id)
            assert response.status_code == 200, "Delete session failed"

        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")
