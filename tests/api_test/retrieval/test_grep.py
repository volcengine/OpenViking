import json

import pytest
import requests


class TestGrep:
    def test_grep_basic(self, api_client):
        try:
            response = api_client.grep("viking://user/", "viking")
            print(f"\nGrep API status code: {response.status_code}")

            if response.status_code == 200:
                data = response.json()
                print("\n" + "=" * 80)
                print("Grep API Response:")
                print("=" * 80)
                print(json.dumps(data, indent=2, ensure_ascii=False))
                print("=" * 80 + "\n")

                assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
                assert data.get("error") is None, (
                    f"Expected error to be null, got {data.get('error')}"
                )
                assert "result" in data, "'result' field should exist"
                assert data["result"] is not None, "'result' should not be null"
                assert "matches" in data["result"], "'matches' field should exist"
                assert isinstance(data["result"]["matches"], list), "'matches' should be a list"
            else:
                print(f"Grep returned non-200 status: {response.status_code}")
                print(f"Response: {response.text}")

        except requests.exceptions.ConnectionError as e:
            print(f"Connection error: {e}")
            pytest.fail("Could not connect to server service - service is not running")
