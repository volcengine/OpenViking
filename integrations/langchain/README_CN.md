# langchain-openviking

`langchain-openviking` 是 OpenViking 官方维护的 LangChain 和 LangGraph
集成包。框架适配逻辑不再依赖 OpenViking 服务端实现，远程访问统一通过轻量的
`openviking-sdk` 完成。

## 安装

LangChain Retriever、Tools、Message History 和 Context Wrapper：

```bash
pip install langchain-openviking
```

LangGraph Store 和 Middleware：

```bash
pip install "langchain-openviking[langgraph]"
```

## 快速开始

```python
from langchain_openviking import OpenVikingRetriever
from openviking_sdk import SyncHTTPClient

client = SyncHTTPClient(
    url="http://127.0.0.1:1933",
    api_key="your-user-api-key",
)
client.initialize()
retriever = OpenVikingRetriever(
    client=client,
    target_uri="viking://user/memories",
)

try:
    documents = retriever.invoke("需要记住哪些部署偏好？")
finally:
    client.close()
```

外部传入的 client 仍由调用方管理。通过 `url=` 创建的 client 由适配器管理。

完整 `openviking` 包会继续保留原有的
`openviking.integrations.langchain` 导入路径，并转发到本包，方便现有应用平滑迁移。
