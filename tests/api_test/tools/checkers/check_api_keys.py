api_keys_path = "/usr/local/lib/python3.11/site-packages/openviking/server/api_keys.py"
print(f"Reading {api_keys_path}...")
print("=" * 80)

with open(api_keys_path, "r") as f:
    print(f.read())
