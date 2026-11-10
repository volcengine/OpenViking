## 步骤 1：安装 Python SDK
在运行代码的 Python 环境中安装独立 SDK：

```bash
python -m pip install --upgrade openviking-sdk
```

## 步骤 2 初始化客户端
使用当前服务地址和 API Key 创建客户端，后续步骤复用该客户端：

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


## 步骤 3：写入资源
将本地文件路径和目标 URI 替换为实际值。`to` 是完整目标 URI，请使用尚未占用的路径：

```python
file_path = "[TODO]your-file-path"
resource_to = "[TODO]your-resource-path" # e.g. viking://resources/my-document
reason = "[TODO]your-reason" # e.g. External API documentation

# Reuse the initialized client.
result = client.add_resource(
    path=file_path,
    to=resource_to,
    options={"reason": reason},
)
```

## 步骤 4：写入记忆
创建会话、写入消息，再提交记忆提取任务：

```python
text = "[TODO]your-message-text" # e.g. I am a developer

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

资源导入和记忆提取都在后台处理。响应包含 `task_id` 时，保存它并用 `client.get_task(result["task_id"])` 查询；无待处理内容的 commit 可能不返回任务。等待 `completed` 后再检查结果，遇到 `failed` 或 `cancelled` 时先处理任务错误。用完后调用 `client.close()`。
