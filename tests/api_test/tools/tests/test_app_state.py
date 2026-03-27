import os

print("Testing app state in container...")
print("=" * 80)

print("\nFirst, let's check what's in /app directory:")
print(os.listdir("/app"))

print("\nNow let's create a simple test script:")
test_script_content = """
import asyncio
from openviking.server.app import create_app
from openviking.server.config import load_server_config

async def test_app_state():
    try:
        config = load_server_config()
        print(f"Config loaded:")
        print(f"  host: {config.host}")
        print(f"  port: {config.port}")
        print(f"  root_api_key: {config.root_api_key}")

        print("\nCreating app...")
        app = create_app(config)

        print("\nApp created! Now entering lifespan...")
        lifespan = app.router.lifespan_context

        print("\n" + "="*80)
        print("Entering lifespan context...")

        async with lifespan(app):
            print("\nInside lifespan context!")
            print(f"app.state attributes: {dir(app.state)}")

            if hasattr(app.state, 'api_key_manager'):
                print(f"\n✅ api_key_manager exists!")
                print(f"  api_key_manager: {app.state.api_key_manager}")
            else:
                print(f"\n❌ api_key_manager does NOT exist!")

            print("\n" + "="*80)
            print("Now let's test _get_api_key_manager function!")
            print("="*80)

            # Let's simulate a request
            class MockRequest:
                def __init__(self, app):
                    self.app = app

            mock_request = MockRequest(app)

            from openviking.server.routers.admin import _get_api_key_manager

            try:
                manager = _get_api_key_manager(mock_request)
                print(f"✅ _get_api_key_manager returned: {manager}")
            except Exception as e:
                print(f"❌ _get_api_key_manager error: {e}")
                import traceback
                traceback.print_exc()

    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(test_app_state())
"""

with open("/app/test_app_state_in_container.py", "w") as f:
    f.write(test_script_content)

print("\nTest script created at /app/test_app_state_in_container.py")
print("\nNow let's run it!")
