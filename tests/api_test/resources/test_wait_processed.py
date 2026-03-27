import json
import time

import pytest
import requests


class TestWaitProcessed:
    def test_wait_processed(self, api_client):
        max_retries = 3
        retry_delay = 2

        for attempt in range(max_retries):
            try:
                response = api_client.wait_processed()
                break
            except requests.exceptions.ConnectionError:
                if attempt < max_retries - 1:
                    print(f"Connection error, retrying in {retry_delay}s...")
                    time.sleep(retry_delay)
                else:
                    pytest.fail("Could not connect to server service - service is not running")

        assert response.status_code < 500, (
            f"Wait processed failed with status {response.status_code}"
        )

        if response.status_code == 200:
            data = response.json()
            print("\n" + "=" * 80)
            print("Wait Processed API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
