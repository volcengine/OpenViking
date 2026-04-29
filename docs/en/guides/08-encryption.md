# Encryption Guide

This guide describes how to enable and use at-rest data encryption in OpenViking.

## Overview

OpenViking provides transparent at-rest data encryption to ensure data security and isolation in multi-tenant environments:

- ✅ **Transparent encryption**: No API changes, application layer unaware
- ✅ **Multi-tenant isolation**: Different accounts use independent keys
- ✅ **Three key providers**: Local, Vault, Volcengine KMS
- ✅ **Backward compatible**: Unencrypted old files still readable

See [Data Encryption](../concepts/10-encryption.md) for conceptual explanations.

## Quick Start

### 1. Initialize Root Key (Local Mode)

```bash
ov system crypto init-key --output ~/.openviking/master.key
```

### 2. Configure Encryption

Edit `~/.openviking/ov.conf`:

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

### 3. Verify

```python
import openviking as ov
import asyncio


async def test():
    client = ov.AsyncOpenViking(path="./data")
    await client.initialize()

    # Add resource (automatically encrypted)
    await client.add_resource("Hello, encrypted world!", reason="Test encryption")

    # Read resource (automatically decrypted)
    results = await client.find("encrypted")
    print(f"Found {len(results)} results")

    await client.close()


asyncio.run(test())
```

Done! Now all written data is automatically encrypted.

## API Key Hashing Configuration

OpenViking provides two layers of encryption protection:

| Encryption Layer | Config | Algorithm | Reversible | Description |
|------------------|--------|-----------|------------|-------------|
| **File Layer** | `encryption.enabled` | AES-GCM | ✅ Yes | Protects entire storage files |
| **API Key Field Layer** | `encryption.api_key_hashing.enabled` | Argon2id | ❌ No | Protects API keys themselves |

### ⚠️ Breaking Change Notice

**Version Change**: OpenViking v0.3.12 → later versions

**Behavior Change**:
- **Before**: `encryption.enabled = true` implicitly enabled API key Argon2id hashing
- **Now**: You must explicitly configure `encryption.api_key_hashing.enabled`

**Impact**:
- After upgrade, if `encryption.enabled = true` but `encryption.api_key_hashing.enabled` is not explicitly set to `true`, you will see the following warning log on startup:
  ```
  API key hashing is disabled while file encryption is enabled.
  Previously, encryption.enabled=true implicitly enabled API key Argon2id hashing.
  Now, API keys will be stored in plaintext within AES-GCM encrypted files.
  To maintain the previous behavior, set encryption.api_key_hashing.enabled=true.
  ```

**Migration Options**:

| Option | Config | Behavior |
|--------|--------|----------|
| **Maintain Previous Behavior** | `api_key_hashing.enabled = true` | API keys stored using Argon2id hashing |
| **Recommended New Behavior** | `api_key_hashing.enabled = false` (default) | API keys stored in plaintext (file layer still encrypted) |

### Default Behavior

**By default, `encryption.api_key_hashing.enabled = false`**:
- API keys are stored in plaintext within JSON files
- If `encryption.enabled = true`, the entire file is protected by AES-GCM encryption
- `ov admin list-users` can display the full API key

### Enabling Argon2id Hashing

For maximum API key protection, you can enable Argon2id one-way hashing:

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

**Note**: When enabled:
- API keys are stored using Argon2id one-way hashing
- Plaintext keys cannot be recovered from hash values
- `ov admin list-users` only shows `key_prefix` instead of the full API key
- Plaintext keys are only visible when creating users or regenerating keys

### Configuration Example

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

## Choosing a Key Provider

| Provider | Use Case | Pros | Cons |
|----------|----------|------|------|
| **Local** | Dev environments, single-node | Simple, no external services | Key stored locally, less secure |
| **Vault** | Production, multi-cloud | Enterprise-grade KMS, version control | Requires deploying and maintaining Vault |
| **Volcengine KMS** | Volcengine cloud | Cloud-native KMS service | Volcengine-only |

---

## Local Mode Detailed Guide

### Initialize Root Key

```bash
# Generate and save to specified path
ov system crypto init-key --output ~/.openviking/master.key

# Or use short option
ov system crypto init-key -o ~/.openviking/master.key
```

**Output example**:
```
✓ Root key generated successfully
✓ Saved to: /Users/you/.openviking/master.key
```

### Security Tips

- ⚠️ Keep `master.key` safe
- Recommend setting file permissions to `600` (owner-only read/write)
- Regularly back up the key file
- Don't commit the key file to version control

### Configuration Example

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

## Vault Mode Detailed Guide

### Prerequisites

1. HashiCorp Vault service deployed
2. Transit engine enabled
3. Vault Token with sufficient permissions

### Configure Vault

1. Enable Transit engine (if not already enabled):

```bash
vault secrets enable transit
```

2. Enable KV engine (if not already enabled):

```bash
# KV v2 (recommended)
vault secrets enable -version=2 kv

# Or KV v1
vault secrets enable kv
```

3. Configure OpenViking:

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

**Configuration Parameters**:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `address` | Vault server address | Required |
| `token` | Vault authentication token | Required |
| `mount_point` | Transit engine mount path | `"transit"` |
| `kv_mount_point` | KV engine mount path | `"secret"` |
| `kv_version` | KV engine version (1 or 2) | `1` |
| `root_key_name` | Key name in Transit engine | `"openviking-root-key"` |
| `encrypted_root_key_key` | Path to store encrypted root key in KV engine | `"openviking-encrypted-root-key"` |

### Vault Permission Recommendations

Configure minimal permissions for the Token:

```hcl
path "transit/encrypt/openviking-root" {
  capabilities = ["update"]
}

path "transit/decrypt/openviking-root" {
  capabilities = ["update"]
}
```

---

## Volcengine KMS Mode Detailed Guide

### Prerequisites

1. Volcengine KMS service activated
2. Symmetric key created
3. Valid Access Key and Secret Key

### Create KMS Key

1. Visit [Volcengine KMS Console](https://console.volcengine.com/kms)
2. Click "Create Key"
3. Select "Symmetric Key", algorithm `AES_256`
4. Record the Key ID

### Configure OpenViking

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

**Configuration Parameters**:

| Parameter | Description | Default |
|-----------|-------------|---------|
| `key_id` | KMS Key ID | Required |
| `region` | Region | Required |
| `access_key` | Access Key | Required |
| `secret_key` | Secret Key | Required |
| `endpoint` | Custom KMS endpoint (optional) | `null` (use default endpoint) |
| `key_file` | Local cache file path for encrypted root key | `"~/.openviking/openviking-volcengine-root-key.enc"` |

### Permission Recommendations

Configure minimal permissions for the Access Key:

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

## Verifying Encryption

### Method 1: Check File Content

Encrypted files start with magic number `OVE1`:

```bash
# View first 4 bytes
hexdump -C ./data/agfs/your-file | head -1
```

**Encrypted file**:
```
00000000  4f 56 45 31 01 01 00 00  00 20 8a 7b 2c 9d 1e  |OVE1..... .{,..|
```
(First 4 bytes are `4f 56 45 31` = "OVE1")

**Unencrypted file**:
```
00000000  7b 22 63 6f 6e 74 65 6e  74 73 22 3a 5b 7b 22 70  |{"contents":[{"p|
```

### Method 2: Cross-Provider Verification

Try decrypting with different providers — it should fail (this is normal security behavior):

```python
# Encrypt with Provider A
encrypted = await provider_a.encrypt_file_key(plaintext, "test-account")

# Try decrypting with Provider B (should fail)
try:
    await provider_b.decrypt_file_key(encrypted, "test-account")
    print("❌ Security vulnerability: Cross-provider decryption succeeded!")
except Exception as e:
    print("✓ Secure: Cross-provider decryption failed as expected")
```

---

## Migration Guide

### Migrating from Unencrypted to Encrypted

1. Back up existing data
2. Enable encryption (see above)
3. Re-import all resources:

```python
import openviking as ov
import asyncio


async def migrate():
    client = ov.AsyncOpenViking(path="./data")
    await client.initialize()

    # List all resources
    resources = await client.list_resources()

    for resource in resources:
        # Read old resource (unencrypted)
        content = await client.read_resource(resource["uri"])
        # Re-write (automatically encrypted)
        await client.add_resource(content, reason="Migrate to encrypted storage")

    await client.close()


asyncio.run(migrate())
```

### Switching Key Providers

1. Back up existing data and keys
2. Decrypt all data with old provider
3. Configure new provider
4. Re-encrypt all data

**Note**: This is a destructive operation, recommend testing first in a staging environment.

---

## Troubleshooting

### Key File Not Found

```
Error: Key file not found: ~/.openviking/master.key
```

**Solution**:
1. Check file path is correct
2. Use absolute path
3. Ensure `~` is properly expanded (use `expanduser()`)

### Vault Connection Failed

```
Error: Failed to connect to Vault
```

**Solution**:
1. Check if Vault service is running
2. Verify `address` configuration
3. Check network connectivity and firewall
4. Confirm Token is valid and not expired

### Volcengine KMS Authentication Failed

```
Error: Invalid credentials
```

**Solution**:
1. Check Access Key and Secret Key are correct
2. Confirm key has sufficient permissions
3. Verify region configuration is correct

### Cross-Provider Decryption Failed (This is Normal)

```
Error: KeyMismatchError
```

**Explanation**: This is expected security behavior. Different providers use different root keys and cannot decrypt each other's data.

### Partial Read Returns Ciphertext

If using encrypted files created with an older OpenViking version, partial reads may return ciphertext.

**Solution**: Upgrade to the latest OpenViking version.

---

## Related Documentation

- [Data Encryption](../concepts/10-encryption.md) - Encryption concepts
- [Configuration Guide](./01-configuration.md) - Complete configuration reference
- [Technical Design](../../design/multi-tenant-file-encryption-desigin.md) - Encryption technical design
