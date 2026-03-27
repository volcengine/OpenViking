#!/usr/bin/env python3
import json
import os

config = {
    "server": {
        "host": "0.0.0.0",
        "port": int(os.environ.get("SERVER_PORT", 1933)),
        "root_api_key": os.environ.get("ROOT_API_KEY", "test-root-api-key"),
    },
    "log": {"level": os.environ.get("LOG_LEVEL", "INFO")},
}

config_path = "/etc/openviking/ov.conf"
os.makedirs(os.path.dirname(config_path), exist_ok=True)

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)

print(f"Generated config at {config_path}")
print(json.dumps(config, indent=2))
