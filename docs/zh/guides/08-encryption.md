# 加密指南

本指南介绍如何在 OpenViking 中启用和使用静态数据加密功能。

## 概述

OpenViking 提供透明的静态数据加密，确保多租户环境下的数据安全与隔离：

- ✅ **透明加密**：API 无变化，应用层无感知
- ✅ **多租户隔离**：不同账户使用独立密钥
- ✅ **三种密钥提供程序**：Local、Vault、火山引擎 KMS
- ✅ **向后兼容**：未加密的旧文件仍可正常读取

加密功能的概念说明见 [数据加密](../concepts/10-encryption.md)。

## 快速开始

### 1. 初始化根密钥（Local 模式）

```bash
ov system crypto init-key --output ~/.openviking/master.key
```

### 2. 配置加密

编辑 `~/.openviking/ov.conf`：

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

```python
import openviking as ov
import asyncio


async def test():
    client = ov.AsyncOpenViking(path="./data")
    await client.initialize()

    # 添加资源（自动加密）
    await client.add_resource("Hello, encrypted world!", reason="测试加密")

    # 读取资源（自动解密）
    results = await client.find("encrypted")
    print(f"找到 {len(results)} 个结果")

    await client.close()


asyncio.run(test())
```

完成！现在所有写入的数据都会自动加密。

## 密钥提供程序选择

| 提供程序 | 适用场景 | 优点 | 缺点 |
|---------|---------|------|------|
| **Local** | 开发环境、单节点部署 | 简单，无需外部服务 | 密钥存储在本地，安全性较低 |
| **Vault** | 生产环境、多云部署 | 企业级密钥管理，支持版本控制 | 需要部署和维护 Vault |
| **Volcengine KMS** | 火山引擎云部署 | 云原生密钥管理服务 | 仅限火山引擎环境 |

---

## Local 模式详细指南

### 初始化根密钥

```bash
# 生成并保存到指定路径
ov system  crypto init-key --output ~/.openviking/master.key

# 或者使用简短命令
ov system crypto init-key -o ~/.openviking/master.key
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

1. 已部署 HashiCorp Vault 服务
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
vault secrets enable -version=2 kv

# 或 KV v1
vault secrets enable kv
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
      "kv_version": 1,
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

为 Token 配置最小权限：

```hcl
path "transit/encrypt/openviking-root" {
  capabilities = ["update"]
}

path "transit/decrypt/openviking-root" {
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

加密文件以魔术数 `OVE1` 开头：

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

### 方法二：跨提供程序验证

尝试用不同提供程序解密彼此的数据，应该会失败（这是正常的安全行为）：

```python
# 用 Provider A 加密
encrypted = await provider_a.encrypt_file_key(plaintext, "test-account")

# 尝试用 Provider B 解密（应该失败）
try:
    await provider_b.decrypt_file_key(encrypted, "test-account")
    print("❌ 安全漏洞：跨提供程序解密成功！")
except Exception as e:
    print("✓ 安全：跨提供程序解密失败，符合预期")
```

---

## 迁移说明

### 从无加密迁移到有加密

1. 备份现有数据
2. 启用加密（参考上文）
3. 重新导入所有资源：

```python
import openviking as ov
import asyncio


async def migrate():
    client = ov.AsyncOpenViking(path="./data")
    await client.initialize()

    # 列出所有资源
    resources = await client.list_resources()

    for resource in resources:
        # 读取旧资源（未加密）
        content = await client.read_resource(resource["uri"])
        # 重新写入（自动加密）
        await client.add_resource(content, reason="迁移到加密存储")

    await client.close()


asyncio.run(migrate())
```

### 切换密钥提供程序

1. 备份现有数据和密钥
2. 使用旧提供程序解密所有数据
3. 配置新提供程序
4. 重新加密所有数据

**注意**：这是一个破坏性操作，建议在测试环境先验证。

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

### 跨提供程序解密失败（这是正常的）

```
Error: KeyMismatchError
```

**说明**：这是预期的安全行为。不同提供程序使用不同的根密钥，无法相互解密。

### 部分读取返回密文

如果使用旧版本 OpenViking 创建的加密文件，部分读取可能返回密文。

**解决方案**：升级到最新版本的 OpenViking。

---

## 相关文档

- [数据加密](../concepts/10-encryption.md) - 加密概念说明
- [配置指南](./01-configuration.md) - 完整配置参考
- [技术设计](../../design/multi-tenant-file-encryption-desigin.md) - 加密技术设计文档
