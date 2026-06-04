# SDK Integration

## Step 1: Install OpenViking

Install or upgrade the Python package:

```bash
pip install openviking --upgrade --force-reinstall
```

## Step 2: Initialize the client

```python
from openviking.client import SyncHTTPClient

url = "https://api.vikingdb.cn-beijing.volces.com/openviking"
api_key = "<your-api-key>"
agent_id = "<your-agent-id>"

client = SyncHTTPClient(
    url=url,
    api_key=api_key,
    agent_id=agent_id,
    timeout=120.0,
)
client.initialize()
```

## Step 3: Choose an API Key

Copy the API Key shown in the OpenViking console and pass it to the SDK client.

## Step 4: Add a resource

```python
file_path = "<your-file-path>"
resource_to = "viking://resources"
reason = "External API documentation"

client.add_resource(
    path=file_path,
    to=resource_to,
    reason=reason,
)
```

## Step 5: Add memory

```python
text = "I am a developer"

session = client.create_session()
session_id = session["session_id"]
client.add_message(
    session_id,
    "user",
    parts=[{"type": "text", "text": text}],
)
result = client.commit_session(session_id)
```
