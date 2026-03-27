import json

import pytest
import requests


class TestGetOverview:
    def test_get_overview(self, api_client):
        try:
            response = api_client.get_overview("viking://")
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, f"Get overview failed with status {response.status_code}"

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("Get Overview API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"
            assert data["result"] is not None, "'result' should not be null"
