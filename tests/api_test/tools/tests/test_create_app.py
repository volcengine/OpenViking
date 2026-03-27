from openviking.server.app import create_app
from openviking.server.config import load_server_config

print("Testing create app...")
print("=" * 80)

try:
    config = load_server_config()
    print("load_server_config result:")
    print(f"  host: {config.host}")
    print(f"  port: {config.port}")
    print(f"  root_api_key: {config.root_api_key}")
    print(f"  type(root_api_key): {type(config.root_api_key)}")

    print("\nCreating app...")
    app = create_app(config)

    print("\nApp created successfully!")
    print(f"app.state attributes: {dir(app.state)}")

    if hasattr(app.state, "api_key_manager"):
        print(f"api_key_manager exists: {app.state.api_key_manager}")
    else:
        print("api_key_manager does NOT exist!")

except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()
