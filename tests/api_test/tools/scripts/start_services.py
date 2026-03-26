#!/usr/bin/env python3
import sys
import time

from config import Config
from services import OpenVikingServiceManager


def main():
    manager = OpenVikingServiceManager()

    try:
        if not manager.start_all():
            print("❌ Failed to start services")
            return 1

        print("\n" + "=" * 60)
        print("OpenViking services are running!")
        print("=" * 60)
        print(f"Server: {Config.SERVER_URL}")
        print(f"Console: {Config.CONSOLE_URL}")
        print("=" * 60)
        print("\nPress Ctrl+C to stop services\n")

        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n\nStopping services...")
    finally:
        # Stop services after test session
        manager.stop_all()

    return 0


if __name__ == "__main__":
    sys.exit(main())
