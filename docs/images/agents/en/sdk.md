## Step 1: Install OpenViking

Run the following command to install OpenViking:

```bash
pip install openviking --upgrade --force-reinstall
```

## Step 2: Initialize the client

Refer to the standard write example from GitHub and fill in the API Key and domain automatically:

```python
from openviking.client import SyncHTTPClient

url = "https://api.vikingdb.cn-beijing.volces.com/openviking"
api_key = "[TODO]your-api-key"

client = SyncHTTPClient(
    url=url,
    api_key=api_key,
    timeout=120.0,
)
client.initialize()
```

## Step 3: Add a resource

Refer to the standard resource write example from GitHub and fill in the API Key and domain automatically:

```python
file_path = "[TODO]your-file-path"
resource_to = "[TODO]your-resource-path"  # e.g. viking://resources
reason = "[TODO]your-reason"  # e.g. External API documentation

# Reuse the initialized client.
client.add_resource(
    path=file_path,
    to=resource_to,
    reason=reason,
)
```

## Step 4: Add memory

Refer to the memory write example from GitHub and fill in the API Key and domain automatically:

```python
text = "[TODO]your-message-text"  # e.g. I am a developer

# Reuse the initialized client.
session = client.create_session()
session_id = session["session_id"]
client.add_message(
    session_id,
    "user",
    parts=[{"type": "text", "text": text}],
)
result = client.commit_session(session_id)
```
