import logging

from openviking_cli.utils import get_logger

print("Testing logger...")
print("=" * 80)

logger = get_logger(__name__)
print(f"Logger: {logger}")
print(f"Logger level: {logger.level}")

print(f"\nRoot logger level: {logging.getLogger().level}")

print("\nTesting log messages:")
logger.debug("Debug message")
logger.info("Info message")
logger.warning("Warning message")
logger.error("Error message")

print("\n" + "=" * 80)
print("Checking openviking.server.app logger...")

app_logger = get_logger("openviking.server.app")
print(f"App logger level: {app_logger.level}")
