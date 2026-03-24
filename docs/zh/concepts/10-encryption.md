# 数据加密

OpenViking 提供透明的静态数据加密，确保多租户环境下的数据安全与隔离。

## 概述

### 为什么需要加密

在多租户架构中，不同客户（账户）的资源文件、记忆和技能都存储在共享的 AGFS 实例中。加密确保：

- 即使攻击者获得 AGFS 磁盘访问权限，也无法读取任何客户的明文数据
- 不同账户的数据使用独立密钥加密，实现租户隔离
- 所有加密解密操作集中在 VikingFS 层，AGFS 和外部对象存储只看到密文

### 对谁透明

加密功能对用户和开发者完全透明：

- **客户端 API 无变化**：现有代码无需修改
- **应用层无感知**：读写操作与未加密时完全相同
- **向后兼容**：未加密的旧文件仍可正常读取

## 三层密钥架构

OpenViking 采用信封加密（Envelope Encryption）架构，使用三层密钥体系：

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Root Key（根密钥）                          │
│  • 整个 OpenViking 实例全局唯一                       │
│  • 存储：KMS 服务 / ~/.openviking/master.key         │
│  • 用途：派生所有账户密钥                              │
└────────────────────┬────────────────────────────────────┘
                     │ HKDF 派生
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Account Key（账户密钥，KEK）                │
│  • 每个账户一个独立密钥                                │
│  • 不存储，运行时派生                                  │
│  • 用途：加密该账户下的所有文件密钥                    │
└────────────────────┬────────────────────────────────────┘
                     │ AES-256-GCM 加密
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 3: File Key（文件密钥，DEK）                   │
│  • 每次写操作生成新的随机密钥                          │
│  • 加密后存储在文件头（信封）中                        │
│  • 用途：加密实际文件内容                              │
└─────────────────────────────────────────────────────────┘
```

### 密钥层次说明

| 层级 | 名称 | 说明 | 数量 |
|------|------|------|------|
| **Root Key** | 根密钥 | 整个系统的主密钥，用于派生所有账户密钥 | 1 个实例 |
| **Account Key** | 账户密钥 | 每个账户独立的密钥，从根密钥派生 | 每个账户 1 个 |
| **File Key** | 文件密钥 | 每个文件的一次性随机密钥 | 每次写入 1 个 |

## 密钥提供程序

OpenViking 支持三种密钥提供程序，适应不同的部署场景：

| 提供程序 | 适用场景 | Root Key 存储 | 特点 |
|---------|---------|--------------|------|
| **Local** | 开发环境、单节点部署 | 本地文件 `~/.openviking/master.key` | 简单，无需外部服务 |
| **Vault** | 生产环境、多云部署 | HashiCorp Vault Transit Engine | 企业级密钥管理，支持密钥版本控制 |
| **Volcengine KMS** | 火山引擎云部署 | 火山引擎 KMS | 云原生密钥管理服务 |

### Local（本地文件）

适合开发环境和单节点部署：

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

**初始化命令**：
```bash
ov crypto init-key --output ~/.openviking/master.key
```

### Vault（HashiCorp Vault）

适合生产环境和多云部署：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "hvs.your-vault-token",
      "mount_point": "transit",
      "kv_mount_point": "secret",
      "kv_version": 1,
      "root_key_name": "openviking-root-key",
      "encrypted_root_key_key": "openviking-encrypted-root-key"
    }
  }
}
```

### Volcengine KMS（火山引擎）

适合火山引擎云部署：

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

## 工作原理

### 写流程

```
客户端              VikingFS             FileEncryptor        KeyManager        AGFS
  │                   │                       │                    │             │
  │  write(uri, data) │                       │                    │             │
  │──────────────────>│                       │                    │             │
  │                   │  encrypt(account_id,  │                    │             │
  │                   │           plaintext)  │                    │             │
  │                   │──────────────────────>│                    │             │
  │                   │                       │derive_account_key()│             │
  │                   │                       │───────────────────>│             │
  │                   │                       │<───────────────────│             │
  │                   │                       │  account_key       │             │
  │                   │  1. 生成随机 File Key                        │             │
  │                   │  2. 用 File Key 加密内容                     │             │
  │                   │  3. 用 Account Key 加密 File Key            │             │
  │                   │  4. 构建信封格式                             │             │
  │                   │<──────────────────────│                    │             │
  │                   │  ciphertext           │                    │             │
  │                   │─────────────────────────────────────────────────────────>│
  │                   │                       │                    │  Write      │
  │<──────────────────│                       │                    │             │
  │   success         │                       │                    │             │
```

### 读流程

```
客户端              VikingFS             FileEncryptor          KeyManager        AGFS
  │                   │                       │                     │             │
  │  read(uri)        │                       │                     │             │
  │──────────────────>│                       │                     │             │
  │                   │──────────────────────────────────────────────────────────>│
  │                   │                       │                     │  Read       │
  │                   │<──────────────────────────────────────────────────────────│
  │                   │  raw_bytes            │                     │             │
  │                   │  检查魔术数 == "OVE1"?  │                     │             │
  │                   │  是 → decrypt()       │                      │             │
  │                   │──────────────────────>│                     │             │
  │                   │                       │ derive_account_key()│             │
  │                   │                       │────────────────────>│             │
  │                   │                       │<────────────────────│             │
  │                   │                       │  account_key        │             │
  │                   │  1. 解析信封格式                              │             │
  │                   │  2. 用 Account Key 解密 File Key             │             │
  │                   │  3. 用 File Key 解密内容                      │             │
  │                   │<──────────────────────│                     │             │
  │                   │  plaintext            │                     │             │
  │<──────────────────│                       │                     │             │
  │   content         │                       │                     │             │
```

### 信封格式

加密文件使用统一的信封格式，以魔术数 `OVE1`（OpenViking Encryption v1）开头：

```
┌─────────────────────────────────────────────────────────────┐
│  魔术数   │  版本    │  Provider   │  加密的 File Key  │  ..   │
│  4 字节   │  1 字节  │   1 字节    │     可变长度       │  ...  │
│  "OVE1"  │   0x01  │  0x01=local │                  │  ...  │
└─────────────────────────────────────────────────────────────┘
```

- 如果文件不以 `OVE1` 开头，视为未加密文件，直接返回明文
- 支持向后兼容，旧文件无需迁移

## 多租户隔离

不同账户的数据使用独立的 Account Key 加密：

- 账户 A 的密钥无法解密账户 B 的文件
- 即使 AGFS 被完全访问，没有对应密钥也无法读取数据
- 租户隔离在密钥层面实现，不依赖存储层权限

## 配置示例

详细配置说明请参考 [配置文档](../guides/01-configuration.md#encryption)。

## 相关文档

- [存储架构](./05-storage.md) - VikingFS 和 AGFS 架构
- [配置指南](../guides/01-configuration.md) - 加密配置详解
- [技术设计](../../design/multi-tenant-file-encryption-desigin.md) - 加密技术设计文档
