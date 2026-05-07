import json


class TestObserver:
    def test_observer_queue(self, api_client):
        try:
            response = api_client.observer_queue()
            print(f"\nObserver queue API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Observer Queue API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_observer_vikingdb(self, api_client):
        try:
            response = api_client.observer_vikingdb()
            print(f"\nObserver vikingdb API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Observer VikingDB API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_observer_system(self, api_client):
        try:
            response = api_client.observer_system()
            print(f"\nObserver system API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Observer System API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise

    def test_observer_models(self, api_client):
        try:
            response = api_client.observer_models()
            print(f"\nObserver Models API status code: {response.status_code}")

            data = response.json()
            print("\n" + "=" * 80)
            print("Observer Models API Response:")
            print("=" * 80)
            print(json.dumps(data, indent=2, ensure_ascii=False))
            print("=" * 80 + "\n")

            assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
            assert data.get("error") is None, f"Expected error to be null, got {data.get('error')}"
            assert "result" in data, "'result' field should exist"

        except Exception as e:
            print(f"Error: {e}")
            raise
