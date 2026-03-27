from openviking.server.config import load_server_config

print("Testing config value...")
print("=" * 80)

try:
    config = load_server_config()
    print("Config loaded:")
    print(f"  host: {config.host}")
    print(f"  port: {config.port}")
    print(f"  root_api_key: {config.root_api_key}")
    print(f"  type(root_api_key): {type(config.root_api_key)}")

    if config.root_api_key:
        print("\n✅ root_api_key is present!")
    else:
        print("\n❌ root_api_key is NOT present!")

except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback

    traceback.print_exc()
