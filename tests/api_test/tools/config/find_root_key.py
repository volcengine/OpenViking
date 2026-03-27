import os
import subprocess

import openviking

print("Searching for root_api_key in OpenViking code...")

# Find where openviking is installed

openviking_path = os.path.dirname(openviking.__file__)
print(f"OpenViking path: {openviking_path}")

# Search for root_api_key in the code
print("\nSearching in openviking package...")
try:
    result = subprocess.run(
        ["grep", "-r", "root_api_key", openviking_path], capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    else:
        print("No matches in openviking package")
except Exception as e:
    print(f"Error searching: {e}")

# Also check openviking_cli
try:
    import openviking_cli

    cli_path = os.path.dirname(openviking_cli.__file__)
    print(f"\nOpenViking CLI path: {cli_path}")
    print("\nSearching in openviking_cli package...")
    result = subprocess.run(
        ["grep", "-r", "root_api_key", cli_path], capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    else:
        print("No matches in openviking_cli package")
except Exception as e:
    print(f"Error searching CLI: {e}")

# Also search for "Admin API requires" to find where that error is coming from
print("\n" + "=" * 80)
print("Searching for error message...")
try:
    result = subprocess.run(
        ["grep", "-r", "Admin API requires", openviking_path], capture_output=True, text=True
    )
    if result.stdout:
        print(result.stdout)
    else:
        print("No matches for error message")
except Exception as e:
    print(f"Error searching error message: {e}")
