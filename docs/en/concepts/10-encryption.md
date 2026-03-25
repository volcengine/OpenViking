# Data Encryption

OpenViking provides transparent at-rest data encryption to ensure data security and isolation in multi-tenant environments.

## Overview

### Why Encryption

In a multi-tenant architecture, resources, memories, and skills from different customers (accounts) are stored in a shared AGFS instance. Encryption ensures:

- Even if an attacker gains AGFS disk access, they cannot read any customer's plaintext data
- Different accounts' data is encrypted with independent keys for tenant isolation
- All encryption/decryption operations are centralized at the VikingFS layer; AGFS and external object stores only see ciphertext

### Transparency

Encryption is completely transparent to users and developers:

- **No client API changes**: Existing code works without modification
- **Application layer unaware**: Read/write operations behave exactly like unencrypted
- **Backward compatible**: Unencrypted old files can still be read normally

## Three-Layer Key Architecture

OpenViking uses an Envelope Encryption architecture with a three-layer key system:

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Root Key                                     │
│  • Global unique per OpenViking instance               │
│  • Storage: KMS service / ~/.openviking/master.key    │
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
| **Vault** | Production, multi-cloud | HashiCorp Vault Transit Engine | Enterprise-grade key management, version control |
| **Volcengine KMS** | Volcengine cloud deployments | Volcengine KMS | Cloud-native KMS service |

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
ov crypto init-key --output ~/.openviking/master.key
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

### Write Flow

```
Client              VikingFS             FileEncryptor         KeyManager        AGFS
  │                   │                       │                     │             │
  │  write(uri, data) │                       │                     │             │
  │──────────────────>│                       │                     │             │
  │                   │  encrypt(account_id,  │                     │             │
  │                   │           plaintext)  │                     │             │
  │                   │──────────────────────>│                     │             │
  │                   │                       │ derive_account_key()│             │
  │                   │                       │────────────────────>│             │
  │                   │                       │<────────────────────│             │
  │                   │                       │  account_key        │             │
  │                   │  1. Generate random File Key                              │
  │                   │  2. Encrypt content with File Key                         │
  │                   │  3. Encrypt File Key with Account Key                     │
  │                   │  4. Build envelope format                                 │
  │                   │<──────────────────────│                     │             │
  │                   │  ciphertext           │                     │             │
  │                   │──────────────────────────────────────────────────────────>│
  │                   │                       │                     │  Write      │
  │<──────────────────│                       │                     │             │
  │   success         │                       │                     │             │
```

### Read Flow

```
Client              VikingFS             FileEncryptor         KeyManager        AGFS
  │                   │                       │                     │             │
  │  read(uri)        │                       │                     │             │
  │──────────────────>│                       │                     │             │
  │                   │──────────────────────────────────────────────────────────>│
  │                   │                       │                     │  Read       │
  │                   │<──────────────────────────────────────────────────────────│
  │                   │ raw_bytes             │                     │             │
  │                   │ Check magic == "OVE1"?│                     │             │
  │                   │ Yes → decrypt()       │                     │             │
  │                   │──────────────────────>│                     │             │
  │                   │                       │ derive_account_key()│             │
  │                   │                       │────────────────────>│             │
  │                   │                       │<────────────────────│             │
  │                   │                       │  account_key        │             │
  │                   │  1. Parse envelope format                                 │
  │                   │  2. Decrypt File Key with Account Key                     │
  │                   │  3. Decrypt content with File Key                         │
  │                   │<──────────────────────│                     │             │
  │                   │  plaintext            │                     │             │
  │<──────────────────│                       │                     │             │
  │   content         │                       │                     │             │
```

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
- Backward compatible, old files don't need migration

## Multi-Tenant Isolation

Different accounts' data is encrypted with independent Account Keys:

- Account A's key cannot decrypt Account B's files
- Even with full AGFS access, data can't be read without the corresponding key
- Tenant isolation is implemented at the key layer, not relying on storage permissions

## Configuration Example

See [Configuration Guide](../guides/01-configuration.md#encryption) for detailed configuration.

## Related Documentation

- [Storage Architecture](./05-storage.md) - VikingFS and AGFS architecture
- [Configuration Guide](../guides/01-configuration.md) - Encryption configuration details
- [Technical Design](../../design/multi-tenant-file-encryption-desigin.md) - Encryption technical design
