import json

import pytest
import requests


class TestServerHealthCheck:
    def test_server_health_check(self, api_client):
        try:
            response = api_client.server_health_check()
            print("\n" + "=" * 80)
            print("Health Check API Response:")
            print("=" * 80)
            print(json.dumps(response.json(), indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert response.status_code < 500, (
                f"Server health check failed with status {response.status_code}"
            )

            if response.status_code == 200:
                data = response.json()
                assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
                assert data.get("error") is None, (
                    f"Expected error to be null, got {data.get('error')}"
                )
                assert data.get("healthy") is not None, "'healthy' field should not be null"
                assert "version" in data, "'version' field should exist"
                assert "user_id" in data, "'user_id' field should exist"

        except requests.exceptions.ConnectionError:
            pytest.fail("Could not connect to server service - service is not running")
