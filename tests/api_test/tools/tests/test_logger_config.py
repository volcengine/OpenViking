import logging

from openviking_cli.utils import get_logger

print("Testing logger config...")
print("=" * 80)

logger = get_logger("openviking.server.app")
print(f"Logger: {logger}")
print(f"Logger level: {logger.level}")
print(f"Logger effective level: {logger.getEffectiveLevel()}")

print("\nLogging levels:")
print(f"  DEBUG: {logging.DEBUG}")
print(f"  INFO: {logging.INFO}")
print(f"  WARNING: {logging.WARNING}")
print(f"  ERROR: {logging.ERROR}")

print("\nTesting log messages:")
logger.debug("Debug message - should we see this?")
logger.info("Info message - should we see this?")
logger.warning("Warning message - should we see this?")
logger.error("Error message - should we see this?")

print("\n" + "=" * 80)
print("Root logger:")
root_logger = logging.getLogger()
print(f"Root logger level: {root_logger.level}")
print(f"Root logger effective level: {root_logger.getEffectiveLevel()}")
print(f"Root logger handlers: {root_logger.handlers}")
