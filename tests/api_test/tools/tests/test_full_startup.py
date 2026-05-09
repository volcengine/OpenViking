import asyncio

from openviking.server.app import create_app
from openviking.server.config import load_server_config

print("Testing full startup...")
print("=" * 80)


async def test_lifespan():
    try:
        config = load_server_config()
        print("Config loaded:")
        print(f"  host: {config.host}")
        print(f"  port: {config.port}")
        print(f"  root_api_key configured: {bool(config.root_api_key)}")

        print("\nCreating app...")
        app = create_app(config)

        print("\nApp created! Now entering lifespan...")
        lifespan = app.router.lifespan_context

        print("\n" + "=" * 80)
        print("Entering lifespan context...")

        async with lifespan(app):
            print("\nInside lifespan context!")
            print(f"app.state attributes: {dir(app.state)}")

            if hasattr(app.state, "api_key_manager"):
                print("\n✅ api_key_manager exists!")
                print(f"  api_key_manager: {app.state.api_key_manager}")
            else:
                print("\n❌ api_key_manager does NOT exist!")

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()


asyncio.run(test_lifespan())
