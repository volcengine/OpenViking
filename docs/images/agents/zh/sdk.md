## 步骤 1 安装 OpenViking
执行以下命令以安装 OpenViking

```bash
pip install openviking --upgrade --force-reinstall
```

## 步骤 2 初始化客户端
参考 GitHub 提供的规范写入示例，自动填入 API Key 和域名

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


## 步骤 3：写入资源
参考 GitHub 提供的规范写入示例，自动填入 API Key 和域名

```python
file_path = "[TODO]your-file-path"
resource_to = "[TODO]your-resource-path" # e.g. viking://resources
reason = "[TODO]your-reason" # e.g. External API documentation

# Reuse the initialized client.
client.add_resource(
    path=file_path,
    to=resource_to,
    reason=reason,
)
```

## 步骤 4：写入记忆
参考 GitHub 提供的记忆写入示例，自动填入 API Key 和域名

```python
text = "[TODO]your-message-text" # e.g. I am a developer

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
