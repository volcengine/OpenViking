import json

import pytest
import requests


class TestListSessions:
    def test_list_sessions(self, api_client):
        try:
            response = api_client.list_sessions()
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, (
            f"List sessions failed with status {response.status_code}"
        )

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("List Sessions API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert isinstance(data["result"], list), "'result' should be a list"
