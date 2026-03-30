import os

from openviking.server.config import load_server_config

print("Testing load_server_config directly...")
print("=" * 80)

try:
    config = load_server_config()
    print("Config loaded successfully!")
    print(f"  host: {config.host}")
    print(f"  port: {config.port}")
    print(f"  root_api_key: {config.root_api_key}")
    print(f"  workers: {config.workers}")
except Exception as e:
    print(f"Error loading config: {e}")
    import traceback

    traceback.print_exc()

print("\n" + "=" * 80)
print("Checking environment variables...")
print(f"OPENVIKING_CONFIG_FILE: {os.environ.get('OPENVIKING_CONFIG_FILE')}")

print("\n" + "=" * 80)
print("Checking config file directly...")
try:
    with open("/etc/openviking/ov.conf", "r") as f:
        import json

        data = json.load(f)
        print(f"File content: {json.dumps(data, indent=2)}")
        print(f"\nserver section: {json.dumps(data.get('server', {}), indent=2)}")
        print(f"root_api_key from file: {data.get('server', {}).get('root_api_key')}")
except Exception as e:
    print(f"Error reading file: {e}")
