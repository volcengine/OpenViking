import inspect
import os

import openviking.server

print("Searching for APIKeyManager...")
print("=" * 80)

# List all modules in openviking.server
server_path = os.path.dirname(openviking.server.__file__)
print(f"Server module path: {server_path}")

print("\nListing all .py files in server directory:")
for filename in os.listdir(server_path):
    if filename.endswith(".py") and not filename.startswith("_"):
        print(f"  {filename}")

# Try to find APIKeyManager in all server modules
print("\nSearching for APIKeyManager in server modules...")
for filename in os.listdir(server_path):
    if filename.endswith(".py") and filename != "__init__.py":
        module_name = f"openviking.server.{filename[:-3]}"
        try:
            module = __import__(module_name, fromlist=[""])
            for name, _obj in inspect.getmembers(module):
                if name == "APIKeyManager":
                    print(f"  Found APIKeyManager in {module_name}!")
        except Exception:
            pass
