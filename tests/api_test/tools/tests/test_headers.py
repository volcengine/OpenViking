import json
import sys

import requests

sys.path.insert(0, ".")

from api.client import OpenVikingAPIClient

client = OpenVikingAPIClient()

print("Client configuration:")
print(f"  API Key: {client.api_key}")
print(f"  Account: {client.account}")
print(f"  User: {client.user}")
print(f"  Session headers: {dict(client.session.headers)}")

print("\n" + "=" * 80)
print("Testing admin list accounts with debug...")

url = f"{client.server_url}/api/v1/admin/accounts"
headers = dict(client.session.headers)

print(f"URL: {url}")
print(f"Headers: {json.dumps(headers, indent=2)}")

try:
    response = requests.get(url, headers=headers)
    print(f"\nStatus code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
except Exception as e:
    print(f"Error: {e}")
