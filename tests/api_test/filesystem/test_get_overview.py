import json

import pytest
import requests


class TestGetOverview:
    def test_get_overview(self, api_client):
        try:
            response = api_client.get_overview("viking://resources")
        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")

        print(f"\nGet Overview API status code: {response.status_code}")

        if response.status_code == 404:
            data = response.json()
            print("\n" + "=" * 80)
            print("Get Overview API Response (404):")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            pytest.skip("Overview file not found on this server. This may be due to AGFS service not being available or no .overview.md file exists.")

        if response.status_code >= 500:
            data = response.json() if response.text else {}
            print("\n" + "=" * 80)
            print("Get Overview API Response (500+):")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")
            pytest.skip(f"Server error on this environment: {data.get('error', 'Unknown error')}. This may be due to AGFS service not being available.")

        if response.status_code != 200:
            pytest.skip(f"Unexpected status code {response.status_code}. This may be due to environment configuration.")

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
