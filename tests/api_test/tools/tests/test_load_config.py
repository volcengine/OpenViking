import os
from copy import deepcopy

from openviking.server.config import load_server_config

print("Testing load_server_config directly...")
print("=" * 80)

try:
    config = load_server_config()
    print("Config loaded successfully!")
    print(f"  host: {config.host}")
    print(f"  port: {config.port}")
    print(f"  root_api_key configured: {bool(config.root_api_key)}")
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
        redacted_data = deepcopy(data)
        if "server" in redacted_data and isinstance(redacted_data["server"], dict):
            if "root_api_key" in redacted_data["server"]:
                redacted_data["server"]["root_api_key"] = "<redacted>"
        print(f"File content: {json.dumps(redacted_data, indent=2)}")
        print(f"\nserver section: {json.dumps(redacted_data.get('server', {}), indent=2)}")
        print(
            f"root_api_key configured in file: {bool(data.get('server', {}).get('root_api_key'))}"
        )
except Exception as e:
    print(f"Error reading file: {e}")
