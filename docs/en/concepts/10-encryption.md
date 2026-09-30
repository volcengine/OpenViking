# Data Encryption

OpenViking supports at-rest encryption: it encrypts files before storage and decrypts them for authorized reads. Each account uses a separate account key.

## Overview

### Why Encryption

Multiple accounts can share an AGFS instance. When encryption is enabled:

- Encrypted files require the corresponding keys to decrypt; protect keys separately from data
- Different accounts' data is encrypted with independent keys for tenant isolation
- The RAGFS encryption wrapper handles file reads and writes at runtime; coverage depends on each backend configuration

### Transparency

Enabling encryption preserves the client API:

- **No client API changes**: Existing code works without modification
- **Plaintext responses**: Authorized reads still return decrypted content; unavailable keys or failed ciphertext authentication cause errors
- **Compatible with existing files**: Old plaintext files remain readable; enabling encryption does not encrypt them automatically

## Three-Layer Key Architecture

OpenViking uses an Envelope Encryption architecture with a three-layer key system:

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Root Key                                     │
│  • Global unique per OpenViking instance               │
│  • Storage: local key or KMS/Vault-protected ciphertext    │
│  • Purpose: Derive all account keys                    │
└────────────────────┬────────────────────────────────────┘
                     │ HKDF derivation
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 2: Account Key (KEK)                           │
│  • One independent key per account                     │
│  • Not stored, derived at runtime                      │
│  • Purpose: Encrypt all file keys for this account     │
└────────────────────┬────────────────────────────────────┘
                     │ AES-256-GCM encryption
                     ▼
┌─────────────────────────────────────────────────────────┐
│  Layer 3: File Key (DEK)                              │
│  • New random key generated per write operation        │
│  • Stored encrypted in file header (envelope)          │
│  • Purpose: Encrypt actual file content                │
└─────────────────────────────────────────────────────────┘
```

### Key Hierarchy Summary

| Layer | Name | Description | Quantity |
|-------|------|-------------|----------|
| **Root Key** | Root Key | System master key, used to derive all account keys | 1 per instance |
| **Account Key** | Account Key | Independent key per account, derived from root key | 1 per account |
| **File Key** | File Key | One-time random key per file | 1 per write |

## Key Providers

OpenViking supports three key providers for different deployment scenarios:

| Provider | Use Case | Root Key Storage | Features |
|----------|----------|-----------------|----------|
| **Local** | Dev environments, single-node deployments | Local file `~/.openviking/master.key` | Simple, no external services |
| **Vault** | Production, multi-cloud | Transit-encrypted value stored in Vault KV | Vault protects the root key |
| **Volcengine KMS** | Volcengine cloud deployments | KMS-encrypted value stored in local `key_file` | KMS protects the root key |

### Local (File)

Suitable for development and single-node deployments:

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

**Initialization command**:
```bash
ov system crypto init-key --output-file ~/.openviking/master.key
```

### Vault (HashiCorp Vault)

Suitable for production and multi-cloud deployments:

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

### Volcengine KMS

Suitable for Volcengine cloud deployments:

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

## How It Works

Startup resolves the root key, then RAGFS `EncryptionWrappedFS` handles file content for protected backends. Python key providers load or unwrap the root key; individual file operations do not call KMS to derive account keys.

### Write Flow

```text
Client plaintext → RAGFS encryption wrapper → Backend ciphertext
```

1. Derive the account key from the root key and `account_id` with HKDF-SHA256; it may be cached at runtime.
2. Generate a random File Key and nonces for this encryption.
3. Encrypt content with the File Key using AES-256-GCM, then wrap the File Key with the account key.
4. Persist the envelope header, wrapped File Key, nonces, and content ciphertext.

### Read Flow

```text
Backend file → Check OVE1 → Unwrap File Key → Authenticate and decrypt → Client plaintext
```

For encrypted files, the account key unwraps the File Key, which authenticates and decrypts the content. Old files without `OVE1` are read as plaintext. An incorrect key or failed ciphertext authentication returns an error, not decrypted content.

### Envelope Format

Encrypted files use a unified envelope format starting with the magic number `OVE1` (OpenViking Encryption v1):

```
┌─────────────────────────────────────────────────────────────┐
│  Magic   │ Version │ Provider  │ Encrypted File Key │  ...  │
│  4 bytes │ 1 byte  │  1 byte   │   Variable length  │  ...  │
│  "OVE1"  │  0x01   │ 0x01=local│                    │  ...  │
└─────────────────────────────────────────────────────────────┘
```

- If a file doesn't start with `OVE1`, it's treated as unencrypted and plaintext is returned directly
- Old files remain readable; protecting existing plaintext requires a separate migration or rewrite

## Multi-Tenant Isolation

Different accounts' data is encrypted with independent Account Keys:

- Account A's key cannot decrypt Account B's files
- For files already encrypted, obtaining backend ciphertext still requires the corresponding key to decrypt it
- Separate keys supplement tenant access controls; they do not replace authentication or storage permissions

## Encryption Coverage

This protects file backends where encryption is enabled. It does not automatically encrypt a separate vector database, logs, or exported OVPack files; vector records can contain abstracts and memory text. Multi-write backups can disable encryption individually, so check each backend configuration.

## Configuration Example

See [Configuration Guide](../guides/01-configuration.md#encryption) for detailed configuration.

## Related Documentation

- [Storage Architecture](./05-storage.md) - VikingFS and AGFS architecture
- [Configuration Guide](../guides/01-configuration.md) - Encryption configuration details
- [Multi-Tenant](./11-multi-tenant.md) - Account, user, and agent isolation model
