logger_path = "/usr/local/lib/python3.11/site-packages/openviking_cli/utils/logger.py"
print(f"Reading {logger_path}...")
print("=" * 80)

with open(logger_path, "r") as f:
    print(f.read())
