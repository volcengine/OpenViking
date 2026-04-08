import json

import pytest
import requests


class TestCreateSession:
    def test_create_session(self, api_client):
        try:
            response = api_client.create_session()
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, (
            f"Create session failed with status {response.status_code}"
        )

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("Create Session API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] is not None, "'result' should not be null"
            assert "session_id" in data["result"], "'session_id' field should exist"
            assert "user" in data["result"], "'user' field should exist"

    def test_create_session_with_custom_id(self, api_client):
        custom_session_id = "test-custom-session-12345"
        try:
            response = api_client.create_session(session_id=custom_session_id)
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, (
            f"Create session with custom ID failed with status {response.status_code}"
        )

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("Create Session with Custom ID Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"]["session_id"] == custom_session_id, (
                f"Expected session_id '{custom_session_id}', got {data['result']['session_id']}"
            )
            assert "user" in data["result"], "'user' field should exist"

            get_response = api_client.get_session(custom_session_id)
            assert get_response.status_code == 200, (
                f"Get session failed with status {get_response.status_code}"
            )

            get_data = get_response.json()
            assert get_data["result"]["session_id"] == custom_session_id, (
                "Retrieved session_id does not match created custom session_id"
            )
