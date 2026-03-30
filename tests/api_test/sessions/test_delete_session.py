import json

import pytest
import requests


class TestDeleteSession:
    def test_delete_session(self, api_client):
        session_id = None

        try:
            response = api_client.create_session()
            assert response.status_code == 200, "Create session failed"
            data = response.json()
            session_id = data["result"]["session_id"]
            assert session_id is not None, "session_id should not be null"

            response = api_client.delete_session(session_id)
            assert response.status_code == 200, "Delete session failed"
            data = response.json()
            print("\n" + "=" * 80)
            print("Delete Session API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")
