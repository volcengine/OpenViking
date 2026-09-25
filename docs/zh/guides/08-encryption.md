# 加密指南

本指南介绍如何在 OpenViking 中启用和使用静态数据加密功能。

## 概述

OpenViking 可以按账户加密存储中新写入的文件。加解密由存储层处理，客户端沿用现有 API：

- **透明加密**：API 无变化，应用层无感知
- **多租户隔离**：不同账户使用独立密钥
- **三种密钥提供程序**：Local、Vault、火山引擎 KMS
- **向后兼容**：未加密的旧文件仍可正常读取

加密功能的概念说明见 [数据加密](../concepts/10-encryption.md)。

## 多写存储中的加密

多写存储复用同一套透明加密机制。加密仍在 RAGFS 内部完成，Python SDK、HTTP API 和 CLI 不需要处理加解密。

规则：

- 全局 `encryption.enabled=true` 时，primary backend 必须加密。
- backup backend 可以通过自己的 `encryption.enabled` 控制是否加密。
- `.redirect.json` 和 `.sync_log.json` 等多写内部元数据跟随 primary 加密策略。
- OpenViking 不提供也不需要公开的加解密 API 来操作这些内部文件。

更多多写配置见 [多写存储指南](./13-multi-write-storage.md)。

## 快速开始

### 1. 初始化根密钥（Local 模式）

```bash
ov system crypto init-key --output-file ~/.openviking/master.key
```

### 2. 配置加密

将下面的加密配置合并到 `~/.openviking/ov.conf`，保留已有模型和服务端配置：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  },
  "storage": {
    "workspace": "./data"
  }
}
```

### 3. 验证

修改加密配置后重启服务，再对该服务运行示例。在运行脚本的环境中安装 [Python SDK](../api/01-overview.md#完全不依赖配置文件使用-python-sdk-客户端)。

```python
import asyncio
from pathlib import Path
from openviking_sdk import AsyncHTTPClient


async def test():
    # 启用认证时，将 OPENVIKING_API_KEY 设置为绑定租户身份的 user/admin key。
    client = AsyncHTTPClient(url="http://localhost:1933")
    try:
        await client.initialize()
        sample = Path("./encrypted-sample.txt")
        sample.write_text("Hello, encrypted world!", encoding="utf-8")
        imported = await client.add_resource(
            path=str(sample),
            wait=True,
            timeout=120,
        )
        results = await client.find(
            query="encrypted", target_uri=imported["root_uri"]
        )
        print(f"找到 {len(results.get('resources', []))} 个资源")
    finally:
        await client.close()


asyncio.run(test())
```

示例会等待导入处理完成并检查检索；检索成功本身不能证明文件已加密。请按下方“验证加密”的文件内容检查步骤确认存储文件头。

## API Key 哈希配置

文件加密和 API Key 哈希是两项独立配置：

| 加密层 | 配置项 | 算法 | 可逆性 | 说明 |
|--------|--------|------|--------|------|
| **文件层** | `encryption.enabled` | AES-GCM | 可逆 | 保护整个存储文件 |
| **API key 字段层** | `encryption.api_key_hashing.enabled` | Argon2id | 不可逆 | 保护 API key 本身 |

### 从隐式 API Key 哈希配置升级

适用于从 v0.3.12 及更早版本升级到 v0.3.13 及之后版本。

**行为变化**：
- **之前**：`encryption.enabled = true` 隐式启用 API key Argon2id 哈希
- **现在**：需要显式配置 `encryption.api_key_hashing.enabled`

**影响**：
- 升级后，如果 `encryption.enabled = true` 但 `encryption.api_key_hashing.enabled` 未显式配置为 `true`，会在启动时看到以下 INFO 日志片段：
  ```
  API key hashing is disabled while file encryption is enabled.
  Previously, encryption.enabled=true implicitly enabled API key Argon2id hashing.
  Now, API keys will be stored in plaintext within AES-GCM encrypted files.
  To maintain the previous behavior, set encryption.api_key_hashing.enabled=true.
  ```

**迁移选项**：

| 选项 | 配置 | 行为 |
|------|------|------|
| **保持原有行为** | `api_key_hashing.enabled = true` | API key 使用 Argon2id 哈希存储 |
| **可恢复的 key 存储（默认）** | `api_key_hashing.enabled = false`（默认） | API key 明文存储（文件层仍加密） |

### 默认行为

**默认情况下，`encryption.api_key_hashing.enabled = false`**：
- API key 以明文存储在 JSON 文件中
- 如果 `encryption.enabled = true`，整个文件会被 AES-GCM 加密保护
- `ov admin list-users` 可以显示完整的 API key

### 启用 Argon2id 哈希

启用 Argon2id 单向哈希后，服务端不再保存可还原的 API Key 明文：

```json
{
  "encryption": {
    "enabled": true,
    "api_key_hashing": {
      "enabled": true
    }
  }
}
```

**注意**：启用后：
- API key 使用 Argon2id 单向哈希存储
- 无法从哈希值还原出明文 key
- `ov admin list-users` 只显示 `key_prefix` 而不是完整的 API key
- 只有在创建用户或重新生成 key 时才能看到明文 key

### 配置示例

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    },
    "api_key_hashing": {
      "enabled": false
    }
  }
}
```

## 密钥提供程序选择

| 提供程序 | 适用场景 | 优点 | 缺点 |
|---------|---------|------|------|
| **Local** | 开发环境、单节点部署 | 简单，无需外部服务 | 需要管理本地文件权限和独立密钥备份 |
| **Vault** | 生产环境、多云部署 | 企业级密钥管理，支持版本控制 | 需要部署和维护 Vault |
| **Volcengine KMS** | 火山引擎云部署 | 云原生密钥管理服务 | 仅限火山引擎环境 |

---

## Local 模式详细指南

### 初始化根密钥

```bash
# 生成并保存到指定路径
ov system crypto init-key --output-file ~/.openviking/master.key

# 或者使用简短命令
ov system crypto init-key -f ~/.openviking/master.key
```

**输出示例**：
```
✓ Root key generated successfully
✓ Saved to: /Users/you/.openviking/master.key
```

### 安全提示

- ⚠️ 妥善保管 `master.key` 文件
- 建议设置文件权限为 `600`（仅所有者可读写）
- 定期备份密钥文件
- 不要将密钥文件提交到版本控制系统

### 配置示例

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}
```

---

## Vault 模式详细指南

### 前置条件

1. 已部署 HashiCorp Vault 服务，并在服务端 Python 环境安装 `hvac`
2. 已启用 Transit 引擎
3. 有足够权限的 Vault Token

### 配置 Vault

1. 启用 Transit 引擎（如果尚未启用）：

```bash
vault secrets enable transit
```

2. 启用 KV 引擎（如果尚未启用）：

```bash
# KV v2（推荐）
vault secrets enable -path=secret -version=2 kv

# 或 KV v1
vault secrets enable -path=secret -version=1 kv
```

按实际部署选择一条 KV 命令，不要两条都执行。下面使用 `secret` 挂载点和 KV v2。先由管理员创建 Transit key：

```bash
vault write -f transit/keys/openviking-root-key type=aes256-gcm96
```

3. 配置 OpenViking：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "hvs.xxxxxxxxxxxxxxxxxxxxx",
      "mount_point": "transit",
      "kv_mount_point": "secret",
      "kv_version": 2,
      "root_key_name": "openviking-root-key",
      "encrypted_root_key_key": "openviking-encrypted-root-key"
    }
  }
}
```

**配置参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `address` | Vault 服务器地址 | 必需 |
| `token` | Vault 认证令牌 | 必需 |
| `mount_point` | Transit 引擎挂载路径 | `"transit"` |
| `kv_mount_point` | KV 引擎挂载路径 | `"secret"` |
| `kv_version` | KV 引擎版本（1 或 2） | `1` |
| `root_key_name` | Transit 引擎中的密钥名称 | `"openviking-root-key"` |
| `encrypted_root_key_key` | KV 引擎中存储加密根密钥的路径 | `"openviking-encrypted-root-key"` |

### Vault 权限建议

对于上面的配置，服务 token 需要读取 Transit key 元数据、加解密权限，以及保存封装根密钥的 KV 读写权限。提前创建引擎和 Transit key 后，可参考：

```hcl
path "transit/keys/openviking-root-key" {
  capabilities = ["read"]
}

path "secret/data/openviking-encrypted-root-key" {
  capabilities = ["read", "create", "update"]
}

path "transit/encrypt/openviking-root-key" {
  capabilities = ["update"]
}

path "transit/decrypt/openviking-root-key" {
  capabilities = ["update"]
}
```

---

## Volcengine KMS 模式详细指南

### 前置条件

1. 已开通火山引擎 KMS 服务
2. 已创建对称密钥
3. 有有效的 Access Key 和 Secret Key

### 创建 KMS 密钥

1. 访问 [火山引擎 KMS 控制台](https://console.volcengine.com/kms)
2. 点击"创建密钥"
3. 选择"对称密钥"，算法选择 `AES_256`
4. 记录密钥 ID

### 配置 OpenViking

```json
{
  "encryption": {
    "enabled": true,
    "provider": "volcengine_kms",
    "volcengine_kms": {
      "key_id": "d926aa0d-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "region": "cn-beijing",
      "access_key": "AKLTxxxxxxxxxxxxxxxxxx",
      "secret_key": "Tmpxxxxxxxxxxxxxxxxxxxxxx",
      "endpoint": null,
      "key_file": "~/.openviking/openviking-volcengine-root-key.enc"
    }
  }
}
```

**配置参数说明**：

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `key_id` | KMS 密钥 ID | 必需 |
| `region` | 区域 | 必需 |
| `access_key` | Access Key | 必需 |
| `secret_key` | Secret Key | 必需 |
| `endpoint` | 自定义 KMS 端点（可选） | `null`（使用默认端点） |
| `key_file` | 加密根密钥本地缓存文件路径 | `"~/.openviking/openviking-volcengine-root-key.enc"` |

### 权限建议

为 Access Key 配置最小权限：

```json
{
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "kms:Encrypt",
        "kms:Decrypt"
      ],
      "Resource": [
        "trn:kms:*:*:key/d926aa0d-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
      ]
    }
  ]
}
```

---

## 验证加密

### 方法一：检查文件内容

检查物理后端文件，API 返回的内容已经解密。下面的路径需换成实际文件路径。加密文件以魔术数 `OVE1` 开头：

```bash
# 查看文件前 4 字节
hexdump -C ./data/agfs/your-file | head -1
```

**加密文件**：
```
00000000  4f 56 45 31 01 01 00 00  00 20 8a 7b 2c 9d 1e  |OVE1..... .{,..|
```
（前 4 字节是 `4f 56 45 31` = "OVE1"）

**未加密文件**：
```
00000000  7b 22 63 6f 6e 74 65 6e  74 73 22 3a 5b 7b 22 70  |{"contents":[{"p|
```

### 方法二：重启后读回

重启前后，通过 API 读取同一份已导入文件，确认内容一致，再按上面的方法核对物理存储文件头。这能检查部署是否重新加载了原密钥，并能读取已有密文。

内部 provider 调用抛出异常不能直接当作加密验证通过。参数错误、依赖缺失和网络错误都可能在解密前失败。

---

## 迁移说明

### 从无加密迁移到有加密

启用加密不会改写已有明文文件。为了向后兼容，这些文件仍可读取；新写入的数据会使用加密。如需加密已有公开 scope，应通过 OVPack 将其迁移到全新的空加密存储环境：

1. 停止业务写入，在原未加密环境运行时创建逻辑备份：

```bash
ov backup ./backups/before-encryption.ovpack
```

2. 停止 OpenViking，启用加密，并将存储配置指向**全新的空** workspace/backend。验证完成前保留原数据和加密密钥备份。
3. 启动加密环境。API Key 模式下，先创建目标 account 和持有 admin key 的恢复操作用户，再让 CLI 使用该 key 连接目标环境，参见 [全量备份和恢复](09-ovpack.md#全量备份和恢复)。恢复过程会通过加密存储层写入 package 内容：

创建 account 会生成 scope 目录，因此 `fail` 会拒绝这次恢复。仅在确认目标只有新建 account 的预置内容后，使用下方的 `overwrite`。如果已有业务数据，先停止操作，按 OVPack 指南备份目标并审查冲突。

```bash
ov restore ./backups/before-encryption.ovpack --on-conflict overwrite
```

4. 切流前验证资源、用户、session 和索引数据。OVPack 不包含 queue、upload、lock、watch 和 relation 文件等运行时/内部状态，这些内容需要单独重建或验证。

支持的 scope 和恢复选项详见 [OVPack 导入与导出](09-ovpack.md#全量备份和恢复)。

### 切换密钥提供程序

保持旧 provider 和密钥可用，导出逻辑 OVPack 备份。按上面的迁移步骤，在使用新 provider 的独立空环境恢复，核对内容、权限和重建后的索引，再切换流量。直接修改原环境的 provider 配置不会重新加密已有文件。

---

## 故障排除

### 密钥文件找不到

```
Error: Key file not found: ~/.openviking/master.key
```

**解决方案**：
1. 检查文件路径是否正确
2. 使用绝对路径
3. 确保 `~` 被正确展开（使用 `expanduser()`）

### Vault 连接失败

```
Error: Failed to connect to Vault
```

**解决方案**：
1. 检查 Vault 服务是否运行
2. 验证 `address` 配置
3. 检查网络连接和防火墙
4. 确认 Token 有效且未过期

### 火山 KMS 认证失败

```
Error: Invalid credentials
```

**解决方案**：
1. 检查 Access Key 和 Secret Key 是否正确
2. 确认密钥有足够权限
3. 验证区域配置正确

### 已有密文无法读取

检查原根密钥、provider 配置、account 身份和密文是否仍然完整。Vault 需要保留 Transit key 和存放封装根密钥的 KV 条目；KMS 需要保留 KMS key 和本地封装密钥文件。不要通过生成新根密钥修复旧密文的读取问题。

部分读取返回密文时，记录服务版本，在测试副本上比较完整读取与部分读取，核对存储加密配置和相关修复后再升级。保留原数据及密钥，以便回退。

---

## 相关文档

- [数据加密](../concepts/10-encryption.md) - 加密概念说明
- [配置指南](./01-configuration.md) - 完整配置参考
- [多租户](../concepts/11-multi-tenant.md) - 账号、用户与 Agent 的隔离模型

Vault 参考：[KV v2](https://developer.hashicorp.com/vault/docs/secrets/kv/kv-v2)、[Transit API](https://developer.hashicorp.com/vault/api-docs/secret/transit)。
