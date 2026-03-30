import json

import requests

print("Testing direct request...")
print("=" * 80)

url = "http://127.0.0.1:1933/api/v1/admin/accounts"
headers = {"Authorization": "Bearer test-root-api-key", "Content-Type": "application/json"}

print(f"URL: {url}")
print(f"Headers: {headers}")

try:
    response = requests.get(url, headers=headers)
    print(f"\nStatus code: {response.status_code}")
    print(f"Response headers: {dict(response.headers)}")
    print("\nResponse content:")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"\nError: {e}")
    import traceback

    traceback.print_exc()
