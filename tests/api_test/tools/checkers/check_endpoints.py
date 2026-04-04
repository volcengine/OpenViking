#!/usr/bin/env python3
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from api.client import OpenVikingAPIClient


def check_endpoints():
    client = OpenVikingAPIClient()

    print("=" * 80)
    print("Checking OpenViking API Endpoints")
    print("=" * 80)

    # Check health first
    try:
        response = client.server_health_check()
        print(f"\n1. /health: {response.status_code}")
        if response.status_code == 200:
            print(f"   Response: {response.json()}")
    except Exception as e:
        print(f"1. /health: ERROR - {e}")

    # Check fs_ls
    try:
        response = client.fs_ls("viking://")
        print(f"\n2. /api/v1/fs/ls: {response.status_code}")
        if response.status_code == 200:
            print(f"   Response: {response.json()}")
            files = response.json().get("result", [])
            if files:
                test_uri = files[0].get("uri")
                if test_uri:
                    # Check fs_stat
                    try:
                        response = client.fs_stat(test_uri)
                        print(f"\n3. /api/v1/fs/stat: {response.status_code}")
                        if response.status_code == 200:
                            print(f"   Response: {response.json()}")
                    except Exception as e:
                        print(f"3. /api/v1/fs/stat: ERROR - {e}")

                    # Check get_overview
                    try:
                        response = client.get_overview(test_uri)
                        print(f"\n4. /api/v1/content/overview: {response.status_code}")
                        if response.status_code == 200:
                            print(f"   Response: {response.json()}")
                    except Exception as e:
                        print(f"4. /api/v1/content/overview: ERROR - {e}")

                    # Check fs_read
                    try:
                        response = client.fs_read(test_uri)
                        print(f"\n5. /api/v1/content/read: {response.status_code}")
                        if response.status_code == 200:
                            print(f"   Response: {response.json()}")
                    except Exception as e:
                        print(f"5. /api/v1/content/read: ERROR - {e}")

                    # Check get_abstract
                    try:
                        response = client.get_abstract(test_uri)
                        print(f"\n6. /api/v1/content/abstract: {response.status_code}")
                        if response.status_code == 200:
                            print(f"   Response: {response.json()}")
                    except Exception as e:
                        print(f"6. /api/v1/content/abstract: ERROR - {e}")
    except Exception as e:
        print(f"2. /api/v1/fs/ls: ERROR - {e}")

    # Check fs_write only when the caller provides an existing file URI.
    write_check_uri = os.getenv("OPENVIKING_WRITE_CHECK_URI")
    if write_check_uri:
        try:
            response = client.fs_write(
                write_check_uri,
                "endpoint write smoke check",
                wait=False,
            )
            print(f"\n7. /api/v1/content/write: {response.status_code}")
            if response.status_code == 200:
                print(f"   Response: {response.json()}")
            else:
                print(f"   Response text: {response.text}")
        except Exception as e:
            print(f"7. /api/v1/content/write: ERROR - {e}")
    else:
        print(
            "\n7. /api/v1/content/write: SKIPPED - set OPENVIKING_WRITE_CHECK_URI to an existing file"
        )

    print("\n" + "=" * 80)


if __name__ == "__main__":
    check_endpoints()
