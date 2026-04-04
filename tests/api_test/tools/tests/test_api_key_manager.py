import asyncio

from openviking.server.app import create_app
from openviking.server.config import load_server_config


async def test_api_key_manager():
    try:
        config = load_server_config()
        app = create_app(config)
        lifespan = app.router.lifespan_context

        async with lifespan(app):
            print(
                "✅ api_key_manager exists!"
                if hasattr(app.state, "api_key_manager")
                else "❌ api_key_manager does NOT exist!"
            )

            print("\n" + "=" * 80)
            print("Testing _get_api_key_manager function!")
            print("=" * 80)

            # Let's simulate a request
            class MockRequest:
                def __init__(self, app):
                    self.app = app

            mock_request = MockRequest(app)

            from openviking.server.routers.admin import _get_api_key_manager

            try:
                manager = _get_api_key_manager(mock_request)
                print(f"✅ _get_api_key_manager returned: {manager}")

                print("\nTesting manager.get_accounts()...")
                accounts = manager.get_accounts()
                print(f"  accounts: {accounts}")
            except Exception as e:
                print(f"❌ _get_api_key_manager error: {e}")
                import traceback

                traceback.print_exc()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback

        traceback.print_exc()


asyncio.run(test_api_key_manager())
