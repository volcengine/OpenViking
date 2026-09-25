# 数据加密

OpenViking 支持静态数据加密，在写入存储前加密文件，并在授权读取时解密。不同 account 使用独立的账户密钥。

## 概述

### 为什么需要加密

多个 account 可以共用 AGFS。启用加密后：

- 已加密文件需要对应密钥才能解密；密钥应与数据分开保护
- 不同账户的数据使用独立密钥加密，实现租户隔离
- 运行时由 RAGFS 的加密包装层处理文件读写；每个后端的加密范围取决于配置

### 对谁透明

启用加密不改变客户端 API：

- **客户端 API 无变化**：现有代码无需修改
- **返回明文**：授权读取仍返回解密后的内容；密钥不可用或密文校验失败时，读写会报错
- **兼容旧文件**：未加密的旧文件仍可读取；开启加密不会自动加密已有文件

## 三层密钥架构

OpenViking 采用信封加密（Envelope Encryption）架构，使用三层密钥体系：

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Root Key（根密钥）                          │
│  • 整个 OpenViking 实例全局唯一                       │
│  • 存储：本地密钥或经 KMS/Vault 保护的密文         │
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
| **Vault** | 生产环境、多云部署 | 经 Transit 加密后存入 Vault KV | 由 Vault 保护根密钥 |
| **Volcengine KMS** | 火山引擎云部署 | 经 KMS 加密后存入本地 `key_file` | 由 KMS 保护根密钥 |

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
ov system crypto init-key --output-file ~/.openviking/master.key
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

运行时在启动阶段取得根密钥，再由 RAGFS `EncryptionWrappedFS` 处理受保护后端的文件内容。Python 的密钥提供程序负责加载或解封根密钥；每次文件读写不需要到 KMS 派生账户密钥。

### 写流程

```text
客户端明文 → RAGFS 加密层 → 后端密文
```

1. 用根密钥和 `account_id` 经 HKDF-SHA256 派生账户密钥（可在运行时缓存）。
2. 为这次加密生成随机 File Key 和 nonce。
3. 用 File Key 对内容执行 AES-256-GCM 加密，再用账户密钥加密 File Key。
4. 将信封头、加密后的 File Key、nonce 和内容密文写入后端。

### 读流程

```text
后端文件 → 检查 OVE1 → 解封 File Key → 校验并解密内容 → 客户端明文
```

读取加密文件时，先用账户密钥解封 File Key，再校验并解密内容。没有 `OVE1` 标记的旧文件按明文读取。密钥错误或密文认证失败会报错，不会返回解密结果。

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
- 旧文件可继续读取；需要保护存量明文时，必须另行迁移或重写

## 多租户隔离

不同账户的数据使用独立的 Account Key 加密：

- 账户 A 的密钥无法解密账户 B 的文件
- 对已经加密的文件，仅取得后端密文仍需要对应密钥才能解密
- 独立密钥补充租户访问控制，不能代替身份认证和存储权限管理

## 加密范围

这里保护的是启用了加密的文件后端。它不自动加密独立向量库、日志或导出的 OVPack；向量记录可能包含摘要和记忆正文。多写 backup 可单独关闭加密，部署时需要逐个检查后端配置。

## 配置示例

详细配置说明请参考 [配置文档](../guides/01-configuration.md#encryption)。

## 相关文档

- [存储架构](./05-storage.md) - VikingFS 和 AGFS 架构
- [配置指南](../guides/01-configuration.md) - 加密配置详解
- [多租户](./11-multi-tenant.md) - 账号、用户与 Agent 的隔离模型
