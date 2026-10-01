## Step 1: Install the Python SDK

Install the standalone SDK in the Python environment that runs your code:

```bash
python -m pip install --upgrade openviking-sdk
```

## Step 2: Initialize the client

Create a client with the service URL and API key. Reuse it in the following steps:

```python
from openviking_sdk import SyncHTTPClient, TextPart

url = "{{OPENVIKING_BASE_URL}}"
api_key = "{{OPENVIKING_API_KEY}}"

client = SyncHTTPClient(
    url=url,
    api_key=api_key,
    timeout=120.0,
)
client.initialize()
```

## Step 3: Add a resource

Replace the local file path and target URI. `to` specifies the complete target URI; use an unused path:

```python
file_path = "[TODO]your-file-path"
resource_to = "[TODO]your-resource-path"  # e.g. viking://resources/my-document
reason = "[TODO]your-reason"  # e.g. External API documentation

# Reuse the initialized client.
result = client.add_resource(
    path=file_path,
    to=resource_to,
    options={"reason": reason},
)
```

## Step 4: Add memory

Create a session, add a message, and submit it for memory extraction:

```python
text = "[TODO]your-message-text"  # e.g. I am a developer

# Reuse the initialized client.
session = client.create_session()
session_id = session["session_id"]
client.add_message(
    session_id=session_id,
    role="user",
    parts=[TextPart(text=text)],
)
result = client.commit_session(session_id=session_id)
```

Resource imports and memory extraction run in the background. When a response contains a `task_id`, save it and check it with `client.get_task(result["task_id"])`. A no-op commit may return no task. Wait for `completed` before checking results; inspect the task error if it is `failed` or `cancelled`. Call `client.close()` when finished.
