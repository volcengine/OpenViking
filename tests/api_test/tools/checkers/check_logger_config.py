import inspect
import os

import openviking_cli.utils

print("Searching for configure_uvicorn_logging...")
print("=" * 80)

print(f"openviking_cli.utils path: {os.path.dirname(openviking_cli.utils.__file__)}")

# List all modules in openviking_cli.utils
utils_path = os.path.dirname(openviking_cli.utils.__file__)
print("\nListing all .py files in utils directory:")
for filename in os.listdir(utils_path):
    if filename.endswith(".py") and not filename.startswith("_"):
        print(f"  {filename}")

# Try to find configure_uvicorn_logging
print("\nSearching for configure_uvicorn_logging in utils modules...")
for filename in os.listdir(utils_path):
    if filename.endswith(".py") and filename != "__init__.py":
        module_name = f"openviking_cli.utils.{filename[:-3]}"
        try:
            module = __import__(module_name, fromlist=[""])
            for name, obj in inspect.getmembers(module):
                if name == "configure_uvicorn_logging":
                    print(f"  Found configure_uvicorn_logging in {module_name}!")
                    print("\nCode for configure_uvicorn_logging:")
                    print(inspect.getsource(obj))
        except Exception:
            pass
