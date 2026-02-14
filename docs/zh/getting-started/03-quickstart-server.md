# 快速开始：服务端模式

将 OpenViking 作为独立 HTTP 服务运行，并从任意客户端连接。

## 前置要求

- 已安装 OpenViking（`pip install openviking`）
- 模型配置已就绪（参见 [快速开始](02-quickstart.md) 了解配置方法）

## 启动服务

确保 `ov.conf` 已配置好存储路径和模型信息（参见 [快速开始](02-quickstart.md)），然后启动服务：

```bash
# 配置文件在默认路径 ~/.openviking/ov.conf 时，直接启动
python -m openviking serve

# 配置文件在其他位置时，通过 --config 指定
python -m openviking serve --config /path/to/ov.conf

# 覆盖 host/port
python -m openviking serve --port 1933
```

你应该看到：

```
INFO:     Uvicorn running on http://0.0.0.0:1933
```

## 验证

```bash
curl http://localhost:1933/health
# {"status": "ok"}
```

## 使用 Python SDK 连接

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")
```

如果服务端启用了认证，需要传入 `api_key`：

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933", api_key="your-key")
```

**完整示例：**

```python
import openviking as ov

client = ov.SyncHTTPClient(url="http://localhost:1933")

try:
    client.initialize()

    # Add a resource
    result = client.add_resource(
        "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"
    )
    root_uri = result["root_uri"]

    # Wait for processing
    client.wait_processed()

    # Search
    results = client.find("what is openviking", target_uri=root_uri)
    for r in results.resources:
        print(f"  {r.uri} (score: {r.score:.4f})")

finally:
    client.close()
```

## 使用 CLI 连接

创建 CLI 连接配置文件 `~/.openviking/ovcli.conf`：

```json
{
  "url": "http://localhost:1933",
  "api_key": "your-key"
}
```

然后直接使用 CLI 命令：

```bash
# Check system health
openviking observer system

# Add a resource to memory
openviking add-resource https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md

# List all synchronized resources
openviking ls viking://resources

# Query
openviking find "what is openviking"
```

如果配置文件在其他位置，通过环境变量指定：

```bash
export OPENVIKING_CLI_CONFIG_FILE=/path/to/ovcli.conf
```

## 使用 curl 连接

```bash
# Add a resource
curl -X POST http://localhost:1933/api/v1/resources \
  -H "Content-Type: application/json" \
  -d '{"path": "https://raw.githubusercontent.com/volcengine/OpenViking/refs/heads/main/README.md"}'

# List resources
curl "http://localhost:1933/api/v1/fs/ls?uri=viking://resources/"

# Semantic search
curl -X POST http://localhost:1933/api/v1/search/find \
  -H "Content-Type: application/json" \
  -d '{"query": "what is openviking"}'
```

## 推荐云端部署方案：火山引擎 ECS

为了获得高性能、可扩展的 Context Memory 能力，能够像“长期记忆”一样为 Agent 提供支持，我们推荐使用 **火山引擎云服务器 (ECS)** 结合 **veLinux** 操作系统进行部署。

### 1. 实例采购配置

在[火山引擎 ECS 控制台](https://console.volcengine.com/ecs/region:ecs+cn-beijing/dashboard?)创建实例时，推荐以下配置：

| 配置项 | 推荐参数 | 说明 |
| :--- | :--- | :--- |
| **镜像** | **veLinux 2.0 (CentOS 兼容版)** | 勾选“安全加固”  |
| **规格** | **计算型 c3a** (2 vCPU, 4GiB 或更高) | 满足基础推理与检索需求 |
| **存储** | **添加数据盘 256 GiB** | 向量数据存储 |
| **网络** | 按需配置 | 建议仅放通所需业务端口 (eg. TCP 1933) |

### 2. 系统环境准备（挂载数据盘）

实例启动后，需将数据盘挂载至 `/data` 目录。请在服务器执行以下命令，自动完成格式化与挂载：

```bash
# 1. 创建挂载点
mkdir -p /data

# 2. 配置自动挂载 (使用 UUID 防止盘符漂移)
cp /etc/fstab /etc/fstab.bak
DISK_UUID=$(blkid -s UUID -o value /dev/vdb)

if [ -z "$DISK_UUID" ]; then
    echo "ERROR: /dev/vdb UUID not found"
else
    # 写入 fstab
    echo "UUID=${DISK_UUID} /data ext4 defaults,nofail 0 0" >> /etc/fstab
    # 验证并挂载
    mount -a
    echo "挂载成功，当前磁盘状态："
    df -Th /data
fi
```

### 3. 安装依赖及OpenViking

```
yum install -y curl git tree

# 第一步：安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 第二步：配置环境变量
echo 'source $HOME/.cargo/env' >> ~/.bashrc

# 配置生效
source ~/.bashrc

# 验证安装
uv --version

# 第三步：在数据盘创建虚拟环境
cd /data

# 创建名为 ovenv 的虚拟环境
uv venv ovenv --python 3.11

# 第四步：激活虚拟环境
source /data/ovenv/bin/activate

# 第五步：验证
echo "Ready"
echo "Python path: $(which python)"
echo "Python version: $(python --version)"
```

- 安装 OpenViking：在激活的虚拟环境下安装工具：

```
uv tool install openviking --upgrade
```

接下来就可以准备配置了。

### 4. OpenViking 服务端配置与启动

配置 AI 模型并让服务在后台常驻。

#### 准备配置文件

在启动服务之前，先建立配置文件目录和文件。

**创建配置目录：**

```
mkdir -p ~/.openviking
```
**创建并编辑配置文件：**

```
vim ~/.openviking/ov.conf
```
配置文件内容可参考
```
{
  "embedding": {
    "dense": {
      "api_base" : "<api-endpoint>",   // API endpoint address (e.g., [https://ark.cn-beijing.volces.com/api/v3](https://ark.cn-beijing.volces.com/api/v3))
      "api_key"  : "<your-api-key>",   // Model service API Key
      "provider" : "<provider-type>",  // Provider type (volcengine or openai)
      "dimension": 1024,               // Vector dimension
      "model"    : "<model-name>",     // Embedding model name (e.g., doubao-embedding-vision-250615)
      "input"    : "multimodal"        // if use doubao-embedding-vision-250615, should be multimodal
    }
  },
  "vlm": {
    "api_base"   : "<api-endpoint>",     // API endpoint address (e.g., [https://ark.cn-beijing.volces.com/api/v3](https://ark.cn-beijing.volces.com/api/v3))
    "api_key"    : "<your-api-key>",     // Model service API Key
    "provider"   : "<provider-type>",    // Provider type (volcengine or openai)
    "max_retries": 2,
    "model"      : "<model-name>"        // VLM model name (e.g., doubao-seed-1-8-251228 or gpt-4-vision-preview)
  }
}
```
- 进入 vim 后按 i 粘贴您的配置内容，完成后按 Esc 输入 :wq 保存退出。

**后台启动服务：**

我们将使用虚拟环境中的程序，并让它在后台运行。
- 激活虚拟环境
```
source /data/ovenv/bin/activate
```
- 创建日志目录
```
mkdir -p /data/log/
```
- 后台启动并重定向输出：
```
nohup openviking-server > /data/log/openviking.log 2>&1 &

# 默认会以执行命令的路径下创建 ./data 存放数据
# 如果后续希望杀死服务进程记得要清理两个后台服务：pkill openviking; pkill agfs
```
可以看到服务在后台常驻运行。当然如果希望重启自动恢复，建议采用 systemctl 启动，此处不赘述。
服务启动后，可以在 /data 下看到数据文件、日志文件等。

**验证服务状态：**

- 检查进程： 输入以下命令查看程序是否在运行：
```
ps aux | grep openviking-server
```

- 查看日志： 如果进程在，看看有没有报错：
```
tail -f /data/log/openviking.log # TODO 支持日志滚动
```
**配置客户端并测试 (CLI)**

本地注意也要先安装 openviking 才能使用 CLI 工具, 然后在 ovcli.conf 配置好服务端地址

- 准备客户端配置：
```
vim ~/.openviking/ovcli.conf
```
- 写入以下内容（注意 IP 换成您服务器的 IP地址）：
```
{
  "url": "http://XXX.XXX.XXX.XXX:1933",
  "api_key": "your-key"
}
```
- 执行系统观察命令，监控系统健康状态：
```
openviking observer system
```
- 功能测试（上传与查找）
```
# 上传测试文件
openviking add-resource https://raw.githubusercontent.com/ZaynJarvis/doc-eval/refs/heads/main/text.md

# 列出资源
openviking ls viking://resources

# 检索测试
openviking find "who is Alice"
```


## 下一步

- [服务部署](../guides/03-deployment.md) - 配置、认证和部署选项
- [API 概览](../api/01-overview.md) - 完整 API 参考
- [认证](../guides/04-authentication.md) - 使用 API Key 保护你的服务
