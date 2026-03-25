bootstrap_path = "/usr/local/lib/python3.11/site-packages/openviking/server/bootstrap.py"
print(f"Reading {bootstrap_path}...")
print("=" * 80)

with open(bootstrap_path, "r") as f:
    print(f.read())
