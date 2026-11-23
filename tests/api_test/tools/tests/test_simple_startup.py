import asyncio

from openviking.server.app import create_app
from openviking.server.config import load_server_config


async def main():
    print("Testing simple startup...")
    print("=" * 80)

    try:
        config = load_server_config()
        print("Config loaded:")
        print(f"  host: {config.host}")
        print(f"  port: {config.port}")
        print(f"  root_api_key configured: {bool(config.root_api_key)}")

        print("\nCreating app...")
        app = create_app(config)

        print("\nApp created! Now checking lifespan...")

        # Let's check what lifespan looks like
        lifespan = app.router.lifespan_context
        print(f"Lifespan: {lifespan}")

        print("\n" + "=" * 80)
        print("Let's try to manually create APIKeyManager!")

        from openviking.server.store_assembly import build_api_key_manager

        manager = build_api_key_manager(config.root_api_key, None)
        print("APIKeyManager created successfully!")

        print("\nTesting manager.list_accounts()...")
        accounts = await manager.list_accounts()
        print(f"  accounts: {accounts}")

    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()


asyncio.run(main())
