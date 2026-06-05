## Step 1: Add a resource

Refer to the resource write example from GitHub and fill in the API Key and domain automatically:

```python
import json
from pathlib import Path

import requests

url = "https://api.vikingdb.cn-beijing.volces.com/openviking"
api_key = "[TODO]your-api-key"
file_path = Path("[TODO]your-file-path")  # e.g. test.txt
resource_to = "[TODO]your-resource-path"  # e.g. viking://resources/test.txt
reason = "[TODO]your-reason"  # e.g. External API documentation

# 1. Initialize request headers.
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer " + api_key,
}

def post_json(path: str, payload: dict, timeout: float):
    response = requests.post(f"{url}{path}", headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()

# 2. Upload the local file to a temporary resource.
with file_path.open("rb") as file:
    result = requests.post(
        f"{url}/api/v1/resources/temp_upload",
        headers={
            "Authorization": "Bearer " + api_key,
        },
        files={"file": (file_path.name, file, "application/octet-stream")},
        timeout=120.0,
    )
result.raise_for_status()
result = result.json()
print(json.dumps(result, ensure_ascii=False, indent=2))
temp_file_id = result["result"]["temp_file_id"]

# 3. Create a resource from the temporary file.
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

## Step 2: Add memory

Refer to the memory write example from GitHub and fill in the API Key and domain automatically:

```python
text = "[TODO]your-message-text"  # e.g. I am a developer

def post_json(path: str, payload: dict, timeout: float):
    response = requests.post(f"{url}{path}", headers=headers, json=payload, timeout=timeout)
    response.raise_for_status()
    return response.json()

# Create a session.
session = post_json("/api/v1/sessions", {}, 360.0)
session_id = session["result"]["session_id"]

# Add a message.
post_json(
    f"/api/v1/sessions/{session_id}/messages",
    {
        "role": "user",
        "parts": [{"type": "text", "text": text}],
    },
    360.0,
)

# Commit the session.
result = post_json(
    f"/api/v1/sessions/{session_id}/commit",
    {"telemetry": False},
    360.0,
)
print(json.dumps(result, ensure_ascii=False, indent=2))
```
