# OpenViking 多租户加密模块

## 概述

OpenViking 多租户加密模块提供了一套完整的端到端加密解决方案，支持多租户数据隔离和安全存储。

## 设计思路

### 三层密钥架构

本模块采用三层密钥架构实现信封加密（Envelope Encryption）：

1. **Root Key（根密钥）**
   - 存储在密钥管理系统（KMS）中
   - 用于派生 Account Key
   - 支持多种密钥提供者（Local File、HashiCorp Vault、火山引擎 KMS）

2. **Account Key（账户密钥）**
   - 通过 HKDF-SHA256 从 Root Key 派生
   - 每个租户独立的密钥
   - 用于加密 File Key

3. **File Key（文件密钥）**
   - 随机生成的 AES-256 密钥
   - 用于加密实际文件内容
   - 使用 Account Key 加密后存储在信封中

### 加密算法

- **对称加密**：AES-256-GCM（AEAD 认证加密）
- **密钥派生**：HKDF-SHA256
- **API 密钥哈希**：Argon2id

## 设计细节

### 模块结构

```
openviking/crypto/
├── __init__.py       # 模块导出
├── config.py         # 加密配置管理
├── encryptor.py     # 文件加密器
├── exceptions.py    # 异常定义
└── providers.py     # 密钥提供者
```

### Envelope 格式

加密后的文件使用自定义 Envelope 格式存储：

```
+----------------+----------------+------------------+------------------+
| Magic (4B)    | Version (1B)  | Provider (1B)  | EFK Len (2B)    |
+----------------+----------------+------------------+------------------+
| KIV Len (2B)   | DIV Len (2B)  | Encrypted File Key (variable) |
+----------------+----------------+------------------------------------+
| Key IV (variable, optional) | Data IV (variable) | Encrypted Content (variable) |
+---------------------------+------------------+---------------------------+
```

- **Magic**: `b"OVE1"` - OpenViking Encryption Version 1
- **Version**: `0x01`
- **Provider**: 密钥提供者类型（0x01=Local, 0x02=Vault, 0x03=Volcengine）
- **EFK Len**: 加密的 File Key 长度
- **KIV Len**: Key IV 长度（仅 Local 提供者使用）
- **DIV Len**: Data IV 长度
- **Encrypted File Key**: 使用 Account Key 加密的 File Key
- **Key IV**: 加密 File Key 使用的 IV（仅 Local 提供者）
- **Data IV**: 加密文件内容使用的 IV
- **Encrypted Content**: 加密的文件内容

## 使用方法

### 1. 配置加密

在 `ov.conf` 中配置加密：

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    },
    "volcengine_kms": {
      "region": "cn-beijing",
      "key_id": "your-key-id",
      "access_key": "your-access-key",
      "secret_key": "your-secret-key"
    }
  }
}
```

### 2. 支持的密钥提供者

#### 本地文件提供者

```python
from openviking.crypto.providers import LocalFileProvider
from openviking.crypto.encryptor import FileEncryptor

provider = LocalFileProvider(key_file="~/.openviking/master.key")
encryptor = FileEncryptor(provider)
```

#### 火山引擎 KMS 提供者

```python
from openviking.crypto.providers import VolcengineKMSProvider
from openviking.crypto.encryptor import FileEncryptor

provider = VolcengineKMSProvider(
    region="cn-beijing",
    access_key_id="your-access-key-id",
    secret_access_key="your-secret-access-key",
    key_id="your-key-id"
)
encryptor = FileEncryptor(provider)
```

### 3. 通过配置启动加密

```python
from openviking.crypto.config import bootstrap_encryption

config = {
    "encryption": {
        "enabled": True,
        "provider": "local",
        "local": {
            "key_file": "~/.openviking/master.key"
        }
    }
}

encryptor = await bootstrap_encryption(config)
```

## 示例代码

### 完整的加密/解密示例

```python
import asyncio
from openviking.crypto.providers import LocalFileProvider
from openviking.crypto.encryptor import FileEncryptor


async def main():
    # 创建密钥提供者和加密器
    provider = LocalFileProvider(key_file="~/.openviking/master.key")
    encryptor = FileEncryptor(provider)
    
    account_id = "test_account"
    plaintext = b"Hello, OpenViking Encryption!"
    
    # 加密
    ciphertext = await encryptor.encrypt(account_id, plaintext)
    print(f"Encrypted: {ciphertext.hex()[:50]}...")
    
    # 解密
    decrypted = await encryptor.decrypt(account_id, ciphertext)
    print(f"Decrypted: {decrypted.decode()}")
    
    assert decrypted == plaintext
    print("Encryption/decryption successful!")


if __name__ == "__main__":
    asyncio.run(main())
```

### 多租户隔离示例

```python
import asyncio
from openviking.crypto.providers import LocalFileProvider
from openviking.crypto.encryptor import FileEncryptor
from openviking.crypto.exceptions import KeyMismatchError


async def main():
    provider = LocalFileProvider(key_file="~/.openviking/master.key")
    encryptor = FileEncryptor(provider)
    
    account1 = "account_1"
    account2 = "account_2"
    secret_data = b"Sensitive data for account 1"
    
    # 使用 account1 加密
    ciphertext = await encryptor.encrypt(account1, secret_data)
    
    # 使用 account1 解密成功
    decrypted1 = await encryptor.decrypt(account1, ciphertext)
    print(f"Account 1 decrypted: {decrypted1.decode()}")
    
    # 使用 account2 解密失败
    try:
        await encryptor.decrypt(account2, ciphertext)
        print("ERROR: Should have failed!")
    except KeyMismatchError:
        print("SUCCESS: Account isolation works correctly!")


if __name__ == "__main__":
    asyncio.run(main())
```

## 测试

### 运行单元测试

```bash
cd /path/to/OpenViking
python -m pytest tests/unit/crypto/ -v
```

### 运行集成测试

```bash
cd /path/to/OpenViking
python -m pytest tests/integration/test_encryption_integration.py -v
```

## 安全特性

1. **多租户隔离**：每个租户的密钥完全隔离
2. **认证加密**：AES-256-GCM 提供完整性和认证
3. **密钥轮换**：支持密钥轮换（通过 KMS 提供者）
4. **向后兼容**：未加密的文件可以正常读取

## 异常处理

| 异常类 | 说明 |
|--------|------|
| `ConfigError` | 配置错误 |
| `KeyMismatchError` | 密钥不匹配 |
| `KeyNotFoundError` | 密钥未找到 |
| `AuthenticationFailedError` | 认证失败 |
| `CorruptedCiphertextError` | 密文损坏 |
| `InvalidMagicError` | 无效的魔数 |

## 注意事项

1. 本地文件提供者的密钥文件权限必须为 0600
2. 火山引擎 KMS 需要正确的访问密钥和区域配置
3. 加密会略微增加文件大小（约 100-200 字节的开销
4. 建议在生产环境使用火山引擎 KMS 或 HashiCorp Vault
