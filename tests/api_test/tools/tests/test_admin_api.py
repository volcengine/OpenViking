import json

from api.client import OpenVikingAPIClient


def _redact_headers(headers):
    redacted = dict(headers)
    for key in list(redacted):
        if key.lower() in {"x-api-key", "authorization"}:
            redacted[key] = "<redacted>"
    return redacted


def main():
    client = OpenVikingAPIClient()

    print("Current API Key configured:", bool(client.api_key))
    print("Current Headers:", _redact_headers(client.session.headers))
    print("\n" + "=" * 80 + "\n")

    print("Testing admin_list_accounts...")
    response = client.admin_list_accounts()
    print(f"Status code: {response.status_code}")
    print("Response:")
    print(json.dumps(response.json(), indent=2, ensure_ascii=False))
    print("\n" + "=" * 80 + "\n")


if __name__ == "__main__":
    main()
