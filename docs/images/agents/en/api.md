# API Integration

## Step 1: Choose an API Key

Copy the API Key shown in the OpenViking console and pass it to your agent environment.

## Step 2: Add a resource

Use the REST API to upload a local file and create a resource:

```python
import json
from pathlib import Path

import requests

url = "https://api.vikingdb.cn-beijing.volces.com/openviking"
api_key = "<your-api-key>"
agent_id = "<your-agent-id>"
file_path = Path("<your-file-path>")
resource_to = "viking://resources/test.txt"
reason = "External API documentation"

headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + api_key,
    "X-OpenViking-Agent": agent_id,
}

def post_json(path: str, payload: dict, timeout: float):
    response = requests.post(f"{url}{path}", headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()

with file_path.open("rb") as file:
    result = requests.post(
        f"{url}/api/v1/resources/temp_upload",
        headers={
            "Authorization": "Bearer " + api_key,
            "X-OpenViking-Agent": agent_id,
        },
        files={"file": (file_path.name, file, "application/octet-stream")},
        timeout=120.0,
    )
result.raise_for_status()
temp_file_id = result.json()["result"]["temp_file_id"]

result = post_json(
    "/api/v1/resources",
    {
        "temp_file_id": temp_file_id,
        "source_name": file_path.name,
        "to": resource_to,
        "reason": reason,
    },
    120.0,
)
print(json.dumps(result, ensure_ascii=False, indent=2))
```

## Step 3: Add memory

Create a session, add a message, and commit the session:

```python
text = "I am a developer"

session = post_json("/api/v1/sessions", {}, 360.0)
session_id = session["result"]["session_id"]

post_json(
    f"/api/v1/sessions/{session_id}/messages",
    {
        "role": "user",
        "parts": [{"type": "text", "text": text}],
    },
    360.0,
)

result = post_json(
    f"/api/v1/sessions/{session_id}/commit",
    {"telemetry": False},
    360.0,
)
print(json.dumps(result, ensure_ascii=False, indent=2))
```
