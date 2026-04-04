# Let's look at the server config module
server_config_path = "/usr/local/lib/python3.11/site-packages/openviking/server/config.py"
print(f"Reading {server_config_path}...")
print("=" * 80)

with open(server_config_path, "r") as f:
    print(f.read())
