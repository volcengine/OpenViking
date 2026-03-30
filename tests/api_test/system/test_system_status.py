import json


class TestSystemStatus:
    def test_system_status(self, api_client):
        try:
            response = api_client.system_status()
            print(f"\nSystem status API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("System Status API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise
