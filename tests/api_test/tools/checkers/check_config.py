config_path = "/usr/local/lib/python3.11/site-packages/openviking/server/config.py"
print(f"Reading {config_path}...")
print("=" * 80)

with open(config_path, "r") as f:
    print(f.read())
