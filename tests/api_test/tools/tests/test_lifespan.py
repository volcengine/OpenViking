import asyncio

from openviking.server.app import create_app
from openviking.server.config import load_server_config

print("Testing lifespan...")
print("=" * 80)


async def test_lifespan():
    try:
        config = load_server_config()
        print("load_server_config result:")
        print(f"  host: {config.host}")
        print(f"  port: {config.port}")
        print(f"  root_api_key: {config.root_api_key}")

        print("\nCreating app...")
        app = create_app(config)

        print("\nGetting lifespan...")
        lifespan = app.router.lifespan_context

        print("\nEntering lifespan context...")
        async with lifespan(app):
            print("Inside lifespan context!")
            print(f"app.state attributes: {dir(app.state)}")

            if hasattr(app.state, "api_key_manager"):
                print(f"api_key_manager exists: {app.state.api_key_manager}")
            else:
                print("api_key_manager does NOT exist!")

    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


asyncio.run(test_lifespan())
