import openviking as ov

client = ov.OpenViking(path="./data")
# client = ov.SyncHTTPClient(url="http://localhost:1933")  # HTTP mode: connect to OpenViking Server

try:
    client.initialize()

    # Add resource (URL, file, or directory)
    res = client.add_resource(
        path="https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = res["root_uri"]
    res = client.ls(root_uri)  # Explore resource tree
    print(f"Directory structure:\n{res}\n")

    res = client.glob(pattern="**/*.md", uri=root_uri)  # use glob to find markdown files
    if res["matches"]:
        content = client.read(res["matches"][0])
        print(f"Content preview: {content[:200]}...\n")

    print(" Wait for semantic processing...")
    client.wait_processed()

    abstract = client.abstract(root_uri)  # Get abstract
    overview = client.overview(root_uri)  # Get overview
    print(f"Abstract:\n{abstract}\n\nOverview:\n{overview}\n")

    results = client.find("what is openviking", target_uri=root_uri)  # Semantic search
    print("Search results:")
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

    client.close()

except Exception as e:
    print(f"Error: {e}")
