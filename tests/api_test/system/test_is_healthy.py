import json


class TestIsHealthy:
    def test_is_healthy(self, api_client):
        try:
            response = api_client.is_healthy()
            print(f"\nIs healthy API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Is Healthy API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"

        except Exception as e:
            print(f"Error: {e}")
            raise
