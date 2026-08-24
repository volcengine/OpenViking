"""Quick start for the OpenViking Python HTTP SDK.

Run these commands first:

    openviking-server init
    openviking-server

Then, in another terminal:

    python examples/quick_start.py
"""

from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(url="http://localhost:1933")

try:
    client.initialize()

    # Add resource (URL, file, or directory) and wait until it is ready to inspect
    print("Wait for semantic processing...")
    res = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md",
        wait=True,
    )
    root_uri = res["root_uri"]
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
