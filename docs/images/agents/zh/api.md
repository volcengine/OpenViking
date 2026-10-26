
## 步骤 1： 写入资源
在运行代码的 Python 环境中安装 `requests`（`python -m pip install requests`）。替换本地文件路径和完整目标 URI，目标需尚未占用；服务地址和 API Key 由页面填入。

```python
import json
from pathlib import Path

import requests

url = "{{OPENVIKING_BASE_URL}}"
api_key = "{{OPENVIKING_API_KEY}}"
file_path = Path("[TODO]your-file-path") # e.g. test.txt
resource_to = "[TODO]your-resource-path" # e.g. viking://resources/test.txt
reason = "[TODO]your-reason" # e.g. External API documentation

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

## 步骤 2： 写入记忆
复用步骤 1 的导入、连接配置和 `post_json` 函数。创建会话、写入消息，再提交记忆提取：

```python
text = "[TODO]your-message-text" # e.g. I am a developer
    
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

资源导入和会话提交可能在后台处理完成前返回。若 `result["result"]` 中包含 `task_id`，用相同鉴权头请求 `GET /api/v1/tasks/{task_id}`，直到状态为 `completed`、`failed` 或 `cancelled`；先处理失败，再读取或检索结果。无待处理内容的 commit 可能不返回任务。
