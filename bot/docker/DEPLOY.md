# Vikingbot 远程容器部署指南（方案一：持久卷）

## 前提条件

- 镜像已推送到火山引擎 ACR
- 已有火山引擎容器实例或 VKE 集群

---

## 步骤一：准备配置文件

### 1.1 复制配置模板

```bash
# 在本地复制配置模板
cp docker/config.example.json ~/.vikingbot/config.json
```

### 1.2 编辑配置

```bash
# 编辑配置文件，填入你的 API keys
vim ~/.vikingbot/config.json
```

至少需要配置：
- `providers.openrouter.apiKey` - OpenRouter API Key
- 可选：channels（Telegram/Discord 等）

---

## 步骤二：火山引擎容器实例部署

### 2.1 创建持久化存储卷

1. 登录火山引擎控制台 → 容器实例
2. 左侧菜单 → 「存储与快照」→ 「云盘」
3. 点击「创建云盘」
   - 名称：`vikingbot-data`
   - 容量：10GB（足够）
   - 可用区：和容器实例同一区

### 2.2 创建容器实例

1. 点击「创建容器实例」
2. **基本配置**：
   - 实例名称：`vikingbot`
   - 规格：2核4GB（推荐）
   - 镜像：选择你的 ACR 镜像
     - 镜像仓库：`vikingbot-cn-beijing.cr.volces.com/vikingbot/vikingbot`
     - 镜像版本：`latest`

3. **存储配置**：
   - 点击「添加云盘」
   - 选择刚才创建的 `vikingbot-data`
   - 挂载路径：`/root/.vikingbot`

4. **网络配置**：
   - 安全组：开放 18790 端口（如需要）

5. **启动命令**：
   - 命令：`vikingbot`
   - 参数：`gateway`

6. 点击「创建」

---

## 步骤三：上传配置文件到云盘

### 方式 A：通过临时容器上传（推荐）

创建一个临时容器来上传配置：

```bash
# 1. 创建一个临时容器，挂载云盘
# 在控制台创建一个临时容器，挂载同一个云盘到 /data

# 2. 本地通过 scp 上传配置到临时容器
scp ~/.vikingbot/config.json root@<临时容器IP>:/data/

# 3. 删除临时容器
```

### 方式 B：先运行初始化命令

1. 先创建容器实例，启动命令设为 `onboard`
2. 容器启动后会自动创建基础目录结构
3. 然后通过控制台「文件管理」或 VNC 上传 `config.json`

---

## 步骤四：启动并验证

### 4.1 启动 gateway

在容器实例详情页，修改启动命令为 `gateway`，然后重启容器。

### 4.2 查看日志

在容器实例详情页 → 「日志」，查看 vikingbot 启动日志。

---

## 后续配置更新

需要修改配置时：

1. 在本地编辑 `~/.vikingbot/config.json`
2. 通过临时容器或文件管理上传到云盘
3. 重启容器实例

---

## 目录结构

云盘挂载后，容器内 `/root/.vikingbot` 目录结构：

```
/root/.vikingbot/
├── config.json          # 配置文件
├── workspace/           # 工作区
├── sandboxes/           # sandbox 数据
└── bridge/              # WhatsApp bridge（已预构建）
```

---

## 常见问题

**Q: 配置文件不生效？**
A: 检查挂载路径是否正确，应该是 `/root/.vikingbot`

**Q: 如何查看配置是否正确加载？**
A: 查看容器日志，或在容器内执行 `vikingbot status`
