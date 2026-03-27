import asyncio

from openviking.server.app import create_app
from openviking.server.config import load_server_config


async def test_simple():
    try:
        config = load_server_config()
        app = create_app(config)
        lifespan = app.router.lifespan_context

        async with lifespan(app):
            print("app.state attributes:", dir(app.state))
            if hasattr(app.state, "api_key_manager"):
                print("✅ api_key_manager exists!")
            else:
                print("❌ api_key_manager does NOT exist!")
    except Exception as e:
        print(f"Error: {e}")
        import traceback

        traceback.print_exc()


asyncio.run(test_simple())
