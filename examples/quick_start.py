"""Quick start for the OpenViking Python HTTP SDK.

Run these commands first:

    openviking-server init
    openviking-server

Then, in another terminal:

    python examples/quick_start.py
"""

import time

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")

try:
    client.initialize()

    # Submit an import, then check its task with separate status requests
    res = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
    )

    task_id = res["task_id"]
    print(f"Import task: {task_id}")
    while True:
        task = client.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Task {task_id} is no longer available")
        if task["status"] == "completed":
            break
        if task["status"] in {"failed", "cancelled"}:
            raise RuntimeError(f"Import task {task_id}: {task['status']} ({task.get('error')})")
        time.sleep(2)
    root_uri = task["result"]["root_uri"]
    res = client.ls(uri=root_uri)  # Explore resource tree
    print(f"Directory structure:\n{res}\n")

    res = client.glob(pattern="**/*.md", uri=root_uri)  # use glob to find markdown files
    if res["matches"]:
        content = client.read(uri=res["matches"][0])
        print(f"Content preview: {content[:200]}...\n")

    abstract = client.abstract(uri=root_uri)  # Get abstract
    overview = client.overview(uri=root_uri)  # Get overview
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    results = client.find(
        query="what is openviking",
        target_uri=root_uri,
    )  # Semantic search
    print("Search results:")
    for result in results.get("resources", []):
        print(f"  {result['uri']} (score: {result.get('score', 0.0):.4f})")

    client.close()

except Exception as e:
    print(f"Error: {e}")
