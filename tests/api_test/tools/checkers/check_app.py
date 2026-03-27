app_path = "/usr/local/lib/python3.11/site-packages/openviking/server/app.py"
print(f"Reading {app_path}...")
print("=" * 80)

with open(app_path, "r") as f:
    print(f.read())
