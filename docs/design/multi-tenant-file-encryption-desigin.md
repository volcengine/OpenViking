> This Design is bilingual (English + Chinese). English version first, Chinese version below.
> 本 设计 为双语版本（英文 + 中文）。英文版在前，中文版在后。

---

# OpenViking Multi-Tenant Encryption Technical Design

## Overview

This document outlines the technical design for implementing at-rest data encryption within the multi-tenant architecture of OpenViking's commercial edition. The core of this solution is a server-side envelope encryption architecture using a derived Root Key. **All encryption and decryption operations are centralized within the VikingFS layer**, ensuring that AGFS and external object storage only ever interact with ciphertext. This approach guarantees that encryption logic remains controllable and decoupled from the underlying storage backend.

This design includes:
- A three-layer key hierarchy (Root Key → Account Key → File Key).
- Support for multiple Root Key providers (local file, HashiCorp Vault, Volcengine KMS).
- Hash-based protection for API Keys.
- Unified encryption and decryption at the VikingFS layer, providing transparent encryption for AGFS file content.
- A `grep` implementation at the VikingFS layer to bypass the ciphertext search limitations of AGFS.
- Encryption for external object storage that is independent of the storage backend's native capabilities.

## Background

### Current Issues

OpenViking is a multi-tenant system where data from different customers (accounts), including resource files, memories, and skills, are stored in a shared server-side AGFS instance. Currently, all sensitive data is stored in plaintext:

| Data Type | Current Storage Location | Current Status |
| :--- | :--- | :--- |
| User API Key | `/{account_id}/_system/users.json` (AGFS) | Plaintext (`secrets.token_hex(32)`) |
| File Content (L0/L1/L2) | `/local/{account_id}/...` (AGFS) | Plaintext UTF-8 text |
| Relational Data | `.relations.json` (AGFS) | Plaintext JSON |
| System Account Table | `/_system/accounts.json` (AGFS) | Plaintext JSON |

### Threats

The **core threat** is that individuals with access to server-side storage (e.g., operations personnel, DBAs, or attackers who compromise the storage system) can directly read any customer's plaintext data.

The **protection goal** is to ensure that even if an attacker gains access to all files on the AGFS disk, they cannot read any customer's file content without the corresponding account's encryption key.

### Encryption Scope and Status

| Data | Encrypted | Method | Status |
| :--- | :--- | :--- | :--- |
| AGFS File Content | ✅ | AES-256-GCM Symmetric Encryption | To be implemented |
| API Key | ✅ | Argon2id One-Way Hash | To be implemented |
| VectorDB | ❌ | Handled by the VectorDB backend itself | Design already exists |
| `ov.conf` Configuration File | ❌ | Not encrypted | Design already exists |

## Goals and Non-Goals

### Goals

1.  **Data-at-Rest Encryption**: All file content stored in AGFS (L0/L1/L2, relations, users.json, accounts.json) will be stored in encrypted form. **All encryption and decryption will be performed at the VikingFS layer.**
2.  **Multi-Tenant Isolation**: Data for different accounts will be encrypted with separate keys to ensure isolation between accounts.
3.  **Zero API Intrusion**: The solution will be fully compatible with existing OpenViking APIs, requiring no changes on the client side.
4.  **Pluggable Architecture**: Support for multiple Root Key storage methods (e.g., local file, HashiCorp Vault, AWS KMS).
5.  **Backward Compatibility**: Support for reading unencrypted files to allow for a smooth migration.
6.  **Acceptable Performance**: The overhead of encryption/decryption will be within an acceptable range (AES-256-GCM with hardware acceleration achieves >1GB/s).
7.  **Storage Backend Agnostic**: The encryption capability will not depend on the native encryption features of external object storage (e.g., TOS/SeaweedFS/RustFS).

### Non-Goals

1.  **Transport Layer Encryption**: This design does not cover TLS/HTTPS or other transport layer encryption, which is assumed to be provided by the deployment environment.
2.  **Client-Side Encryption**: This design does not implement end-to-end encryption (E2EE) on the client side. Encryption is handled at the server-side VikingFS layer.
3.  **VectorDB Encryption**: The encryption of VectorDB is the responsibility of its own backend and is outside the scope of this design.
4.  **Key Rotation**: The MVP version of this solution will not implement automatic key rotation (only interfaces will be reserved).
5.  **AGFS Layer Encryption**: This design does not implement encryption at the AGFS layer; all encryption logic is handled at the VikingFS layer.
6.  **Native External Object Storage Encryption**: This design will not use native encryption capabilities of external storage, such as SSE-TOS/SSE-KMS/SSE-C.

## System Metrics

### Functional Metrics

| Metric | Description | Acceptance Criteria |
| :--- | :--- | :--- |
| File Encryption | Files written to AGFS are automatically encrypted. | After writing a file, the file on disk should start with the `OVE1` magic number and its content should be ciphertext. |
| File Decryption | Files read from AGFS are automatically decrypted. | Reading an encrypted file correctly restores it to plaintext. |
| Multi-Tenant Isolation | Different accounts use independent keys. | The key for Account A cannot decrypt files belonging to Account B. |
| API Key Hashing | API Keys are stored as Argon2id hashes. | The `users.json` file no longer stores plaintext API Keys. |
| Backward Compatibility | Unencrypted files can still be read. | Reading old, unencrypted files correctly returns the plaintext content. |
| Pluggable Providers | Support for various Root Key storage methods. | The system can switch between Local, Vault, and AWS KMS providers via configuration. |

### Performance Metrics

| Metric | Description | Acceptance Criteria |
| :--- | :--- | :--- |
| Encryption Throughput | AES-256-GCM encryption performance. | ≥ 500 MB/s (with hardware acceleration). |
| Decryption Throughput | AES-256-GCM decryption performance. | ≥ 500 MB/s (with hardware acceleration). |
| API Key Verification | Argon2id verification time. | ≤ 100ms per verification. |
| Key Derivation | HKDF-SHA256 derivation time. | ≤ 1ms per derivation. |
| File Read/Write Latency | The impact of encryption on read/write latency. | Increase in latency ≤ 10% (compared to unencrypted). |

## Terminology

The following table explains the cryptographic and cloud security terms used in this design:

| Term | English Full Name | Explanation |
| :--- | :--- | :--- |
| **AES-256-GCM** | Advanced Encryption Standard 256-bit with Galois/Counter Mode | A NIST standard symmetric encryption algorithm with a 256-bit key that provides both encryption and integrity authentication (AEAD), and supports CPU hardware acceleration. |
| **AEAD** | Authenticated Encryption with Associated Data | An encryption scheme that simultaneously provides confidentiality and integrity, preventing data tampering. |
| **HKDF** | HMAC-based Extract-and-Expand Key Derivation Function | An RFC 5869 standard key derivation function used to derive multiple sub-keys from a high-entropy master key. |
| **KMS** | Key Management Service | A service for securely generating, storing, rotating, and destroying encryption keys, typically offered by cloud providers (e.g., AWS KMS, Volcengine KMS). |
| **HashiCorp Vault** | HashiCorp Vault | An open-source key management and secrets storage system that provides "encryption as a service" through its Transit Engine. |
| **Transit Engine** | Vault Transit Engine | HashiCorp Vault's encryption engine, where the Root Key never leaves Vault. It supports key versioning and automatic rotation. |
| **Envelope Encryption** | Envelope Encryption | A hierarchical encryption architecture where a Data Encryption Key (DEK) encrypts data, and a Key Encryption Key (KEK) encrypts the DEK. The KEK is managed by a KMS. |
| **DEK** | Data Encryption Key | The key used to encrypt the actual file content. A new random key is generated for each write operation. |
| **KEK** | Key Encryption Key | The key used to encrypt the DEK. It is derived from the Root Key or managed by a KMS. |
| **Root Key** | Root Key | The master key for the entire encryption system, used to derive all Account Keys. It must be stored securely. |
| **Account Key** | Account Key | An independent encryption key for each tenant, derived from the Root Key via HKDF. It is used to encrypt all File Keys for that account. |
| **File Key** | File Key | A unique, one-time random key for each file, used to encrypt the file's content. |
| **Argon2id** | Argon2id | The winning algorithm of the 2015 Password Hashing Competition (PHC), designed to resist GPU/ASIC brute-force attacks by combining the advantages of Argon2i (side-channel resistance) and Argon2d (GPU resistance). |
| **IV** | Initialization Vector | A value used in symmetric encryption to ensure that the same plaintext produces different ciphertext with the same key, preventing pattern analysis. |
| **GCM Tag** | Galois/Counter Mode Authentication Tag | An authentication tag generated by GCM to verify data integrity and authenticity. Tampered data will fail tag verification. |
| **Magic Number** | Magic Number | A fixed sequence of bytes at the beginning of a file used to identify the file format (this design uses `b"OVE1"` for OpenViking Encryption v1). |
| **SSE** | Server-Side Encryption | Native encryption capabilities provided by object storage services (like S3/TOS). |
| **SSE-KMS** | Server-Side Encryption with KMS | Server-side encryption using KMS-managed keys, supporting per-tenant independent keys. |
| **SSE-C** | Server-Side Encryption with Customer-Provided Keys | Server-side encryption where the customer provides the keys, which are not persistently stored on the cloud server. |

## Functional Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        OpenViking Server                        │
├─────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                     API Layer (No Change)                 │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    VikingFS (Encryption Layer)            │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  FileEncryptor (AES-256-GCM + Envelope)             │  │  │
│  │  │  - encrypt(account_id, plaintext) → ciphertext      │  │  │
│  │  │  - decrypt(account_id, ciphertext) → plaintext      │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   KeyManager (Key Management)             │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  RootKeyProvider (Abstract Interface)               │  │  │
│  │  │  ├─ LocalFileProvider (Dev/Single-Node)             │  │  │
│  │  │  ├─ VaultProvider (Production/Multi-Cloud)          │  │  │
│  │  │  └─ VolcengineKMSProvider (Volcengine Cloud)        │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      AGFS (Storage Layer)                 │  │
│  │  (LocalFS / s3FS / memFS, No Change)                      │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Interface Boundary Diagram

### New Module Interfaces

```
┌─────────────────────────────────────────────────────────────────┐
│                        New Module Interfaces                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.RootKeyProvider (Abstract Base Class)  │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + async get_root_key() -> bytes                          │  │
│  │  + async derive_account_key(account_id: str) -> bytes     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                           ▲                                     │
│          ┌────────────────┼────────────────┐                    │
│          │                │                │                    │
│  ┌───────┴──────┐ ┌───────┴──────┐ ┌─────────────────────┐      │
│  │LocalProvider │ │VaultProvider │ │VolcengineKMSProvider│      │
│  └──────────────┘ └──────────────┘ └─────────────────────┘      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.FileEncryptor                          │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + async encrypt(account_id: str, plaintext: bytes)       │  │
│  │                    -> bytes                               │  │
│  │  + async decrypt(account_id: str, ciphertext: bytes)      │  │
│  │                    -> bytes                               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.APIKeyHasher                           │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + hash(api_key: str) -> str                              │  │
│  │  + verify(stored_hash: str, api_key: str) -> bool         │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### Modified Existing Module Interfaces

```
┌─────────────────────────────────────────────────────────────────┐
│                      Modified Existing Module Interfaces        │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  VikingFS (Modified)                                      │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  __init__(..., encryptor: Optional[FileEncryptor] = None) │  │
│  │  read(..., ctx) → Decrypt (if encrypted file)             │  │
│  │  write(..., ctx) → Encrypt (if enabled)                   │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  APIKeyManager (Modified)                                 │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  load() → Read hashed API Keys                            │  │
│  │  resolve(api_key) → Argon2id verification                 │  │
│  │  register_user() → Store hash                             │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## Technical Architecture Diagram

### Three-Layer Key Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                    Three-Layer Key Architecture (Envelope Encryption)    │
├──────────────────────────────────────────────────────────────────────────┤
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Layer 1: Root Key                                                 │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │  • Mapping: Entire OpenViking instance (globally unique)           │  │
│  │  • Storage: KMS Service / ~/.openviking/master.key                 │  │
│  │  • Manager: System Administrator (ROOT role)                       │  │
│  │  • Purpose: Derive all Account Keys                                │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                         │ HKDF-SHA256(Root Key, account_id)              │
│                         ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Layer 2: Account Key (Key Encryption Key, KEK)                    │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │  • Mapping: One account (a workspace in APIKeyManager)             │  │
│  │  • Quantity: One per account_id                                    │  │
│  │  • Storage: Not stored, derived at runtime                         │  │
│  │  • Isolation: Team A's key cannot decrypt Team B's files           │  │
│  │  • Purpose: Encrypt all File Keys under the account                │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                         │ AES-256-GCM(account key, file key)             │
│                         ▼                                                │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  Layer 3: File Key (Data Encryption Key, DEK)                      │  │
│  ├────────────────────────────────────────────────────────────────────┤  │
│  │  • Mapping: A single file in AGFS (L0/L1/L2, relations, users)     │  │
│  │  • Quantity: A new one is generated for each VikingFS.write() call │  │
│  │  • Storage: Encrypted and stored in the file header (Envelope)     │  │
│  │  • Purpose: Encrypt the actual file content                        │  │
│  └────────────────────────────────────────────────────────────────────┘  │
│                                                                          │
└──────────────────────────────────────────────────────────────────────────┘
```

### Data Flow Diagram

#### Write Process

```
Client                    VikingFS                FileEncryptor          KeyManager           AGFS
  │                          │                         │                     │                │
  │  write(uri, content)     │                         │                     │                │
  │────────────────────────────>│                      │                     │                │
  │                          │  encrypt(account_id,    │                     │                │
  │                          │           plaintext)    │                     │                │
  │                          │────────────────────────>│                     │                │
  │                          │                         │ derive_account_key()│                │
  │                          │                         │────────────────────>│                │
  │                          │                         │<────────────────────│                │
  │                          │                         │  account_key        │                │
  │                          │                         │                     │                │
  │                          │  1. Generate file_key   │                     │                │
  │                          │  2. Encrypt content     │                     │                │
  │                          │     with file_key       │                     │                │
  │                          │  3. Encrypt file_key    │                     │                │
  │                          │     with account_key    │                     │                │
  │                          │  4. Build envelope      │                     │                │
  │                          │<────────────────────────│                     │                │
  │                          │  ciphertext             │                     │                │
  │                          │───────────────────────────────────────────────────────────────>│
  │                          │                         │                     │                │ Write
  │<─────────────────────────│                         │                     │                │
  │   success                │                         │                     │                │
```

#### Read Process

```
Client                    VikingFS                FileEncryptor          KeyManager           AGFS
  │                          │                         │                     │                │
  │  read(uri)               │                         │                     │                │
  │─────────────────────────>│                         │                     │                │
  │                          │───────────────────────────────────────────────────────────────>│
  │                          │                         │                     │                │ Read
  │                          │<───────────────────────────────────────────────────────────────│
  │                          │  raw_bytes              │                     │                │
  │                          │  Check magic == "OVE1"? │                     │                │
  │                          │  Yes → decrypt()        │                     │                │
  │                          │────────────────────────>│                     │                │
  │                          │                         │ derive_account_key()│                │
  │                          │                         │────────────────────>│                │
  │                          │                         │<────────────────────│                │
  │                          │                         │  account_key        │                │
  │                          │                         │                     │                │
  │                          │  1. Parse envelope      │                     │                │
  │                          │  2. Decrypt file_key    │                     │                │
  │                          │     with account_key    │                     │                │
  │                          │  3. Decrypt content     │                     │                │
  │                          │     with file_key       │                     │                │
  │                          │<────────────────────────│                     │                │
  │                          │  plaintext              │                     │                │
  │<─────────────────────────│                         │                     │                │
  │   content                │                         │                     │                │
```

## Data Model Design

### Encrypted File Format (Envelope v1)

```
Envelope format:
Offset  Size    Field                   Description
0x00    4       magic                   b"OVE1" (OpenViking Encryption v1)
0x04    1       version                 0x01 (Version number)
0x05    1       provider_type           0x01=local, 0x02=vault, 0x03=volcengine_kms
0x06    2       encrypted_key_length    Big-endian, length of the encrypted File Key
0x08    2       key_iv_length           Big-endian, length of the Key IV (12 for Local mode, 0 for other modes)
0x0A    2       data_iv_length          Big-endian, length of the Data IV (fixed at 12)
0x0C    var     encrypted_file_key      The encrypted File Key
var     var     key_iv                  Local mode: IV for encrypting the File Key; Other modes: empty
var     var     data_iv                 IV for encrypting the file content (12 bytes)
var     var     encrypted_content       The encrypted file content (including a 16-byte GCM auth tag)
```

**Magic Number Explanation**:

- `b"OVE1"`: Indicates that this is an encrypted file in OpenViking Encryption v1 format.
- If a file does not start with `b"OVE1"`, it is treated as an unencrypted file, and its plaintext is returned directly.

### API Key Storage Format

**Before Modification** (plaintext storage):

```json
{
  "users": {
    "alice": {
      "role": "admin",
      "key": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    }
  }
}
```

**After Modification** (hash storage):

```json
{
  "users": {
    "alice": {
      "role": "admin",
      "key_hash": "$argon2id$v=19$m=65536,t=3,p=4$...",
      "key_prefix": "sk-abcde"
    }
  }
}
```

**Field Explanation**:

- `key_hash`: The Argon2id hash value (including algorithm parameters and salt).
- `key_prefix`: The first 8 characters of the API Key, used for O(1) fast lookup of candidate entries.

### Configuration File Format (Additions to ov.conf)

```
# Local file provider
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}

# Vault provider
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "vault-token-xxx",
      "mount_point": "transit",
      "key_name": "openviking-root"
    }
  }
}

# Volcengine KMS provider
{
  "encryption": {
    "enabled": true,
    "provider": "volcengine_kms",
    "volcengine_kms": {
      "key_id": "kms-key-id-xxx",
      "region": "cn-beijing",
      "access_key": "AKLTxxxxxxxx",
      "secret_key": "Tmpxxxxxxxx"
    }
  }
}
```

### Configuration Class Structure

The configuration class includes various fields for encryption settings, such as whether encryption is enabled, the key provider type, and specific configuration parameters for each provider. The class uses `BaseModel` for type validation to ensure the configuration is correct.

## Key Solution Design

### 1. Encryption Algorithm Selection

#### Symmetric Encryption: AES-256-GCM

**Reason for selection**:

- **AEAD** (encryption + authentication in one), providing both encryption and tamper-proofing in a single step.
- **CPU hardware acceleration** (AES-NI), with throughput >1 GB/s.
- **Standard algorithm** for TLS 1.3 / AWS S3 / GCP.
- **NIST SP 800-38D standard**.

**Parameters**:

- **Key length**: 256-bit
- **IV**: 96-bit (randomly generated)
- **Authentication tag**: 128-bit

#### Key Derivation: HKDF-SHA256

**Reason for selection**:

- **Designed for this specific scenario** (RFC 5869).
- **Microsecond-level computation overhead**.
- **Deterministic output**.

**Parameters**:

- **Algorithm**: SHA-256
- **Salt**: Fixed value `b"openviking-kek-salt-v1"`
- **Info**: `f"openviking:kek:v1:{account_id}".encode()`

#### API Key Hashing: Argon2id

**Reason for selection**:

- **RFC 9106 standard**, 2015 PHC winner.
- **Strong resistance to GPU/ASIC attacks**.
- **Configurable memory hardness**.

**Parameters**:

- **Variant**: Argon2id (a hybrid of Argon2i + Argon2d)
- **Memory**: 64 MiB
- **Iterations**: 3
- **Parallelism**: 4

### 2. RootKeyProvider Abstract Design

`RootKeyProvider` is the core abstraction of the entire encryption system, defining a unified interface for key management. All concrete key providers (`LocalFileProvider`, `VaultProvider`, `VolcengineKMSProvider`) must implement this interface.

**Core Methods**:
- `get_root_key()`: Retrieves the Root Key (used only by the Local Provider).
- `derive_account_key(account_id)`: Derives the Account Key for a specific account.
- `encrypt_file_key(plaintext_key, account_id)`: Encrypts a File Key.
- `decrypt_file_key(encrypted_key, account_id)`: Decrypts a File Key.

### 3. LocalFileProvider Implementation

`LocalFileProvider` is the simplest Root Key provider, suitable for development environments and single-node deployments. The Root Key is stored in hexadecimal format in a local file.

**Core Functionality**:
- **Root Key Storage**: Local file (in hexadecimal format).
- **Account Key Derivation**: Derived from the Root Key using HKDF-SHA256.
- **File Key Encryption/Decryption**: Uses AES-256-GCM.

#### Local Root Key File Format

**File Location**: `~/.openviking/master.key` (default)

**File Format**:
- **Content**: Hexadecimal encoding of a 32-byte random key (64 characters).
- **File Permissions**: `chmod 0600` (only owner can read and write).

**Initialization Process**:
1. Read the key file.
2. Verify file permissions (must be `0600`).
3. Parse the hexadecimal content.
4. Verify the key length (must be 32 bytes).
5. Cache the key in memory.

**CLI Command**:
```bash
ov system crypto init-key --output ~/.openviking/master.key
```
- If the target file already exists, an error is reported, and the process exits.
- Generates a 32-byte random key.
- Writes the key to the file in hexadecimal format.
- Sets file permissions to `0600`.

The `LocalFileProvider` implements all methods of the `RootKeyProvider` interface. During initialization, it receives the key file path as a parameter. The `get_root_key` method is responsible for reading and caching the root key from the file. The first call reads the file, and subsequent calls directly return the cached key. The `derive_account_key` method first obtains the root key, then uses the HKDF algorithm to derive the account key based on the account ID. The `encrypt_file_key` method first derives the account key, generates a random IV, then encrypts the File Key using the AES-GCM algorithm, returning the encrypted key and IV. The `decrypt_file_key` method performs the reverse operation, decrypting the File Key using the account key and IV.

### 4. FileEncryptor Implementation

`FileEncryptor` is the core component for file encryption, implementing the Envelope Encryption pattern.

**Core Functionality**:
- **Envelope Format**: Magic (4B) + Provider Type (1B) + Key Length (2B) + Encrypted File Key + Key IV (Local) + Data IV + Encrypted Content.
- **Magic Number Detection**: Automatically identifies encrypted files using the `b"OVE1"` magic number.
- **Backward Compatibility**: Unencrypted files are returned directly, supporting a smooth migration.

Upon initialization, the `FileEncryptor` receives a `RootKeyProvider` instance and automatically detects the provider type. The encryption process is as follows: first, a random File Key is generated, followed by a random Data IV. The file content is then encrypted using the File Key and Data IV with AES-GCM. Next, the File Key is encrypted based on the provider type: for Local mode, the provider's `encrypt_file_key` method is used to encrypt the File Key and obtain the `key_iv`; for other modes, only the encrypted File Key is obtained, and `key_iv` is empty. Finally, all components are assembled into the Envelope format and returned. The decryption process first checks the magic number; if it's not an encrypted file, it's returned directly. If it is an encrypted file, the Envelope is parsed to extract the provider type, encrypted File Key, `key_iv`, `data_iv`, and encrypted content. The File Key is then decrypted based on the provider type: Local mode requires the `key_iv`, while other modes do not. Finally, the file content is decrypted using the decrypted File Key and `data_iv`.

### 5. VikingFS Integration

**The VikingFS layer is the sole point of encryption and decryption**. All file content encryption and decryption are performed here, with AGFS and external object storage only seeing ciphertext.

**Core Changes**:
- **Added `encryptor` parameter**: The `__init__` method now accepts a `FileEncryptor`.
- **Automatic decryption in `read()`**: Files are automatically checked and decrypted after being read (the only decryption point in the VikingFS layer).
- **Automatic encryption in `write()`**: Files are automatically encrypted before being written (the only encryption point in the VikingFS layer).
- **`grep()` implemented at the VikingFS layer**: Not reliant on AGFS's `grep`, it reads, decrypts, and searches on its own.

`VikingFS` receives and saves the `encryptor` parameter during initialization. It provides two helper methods, `_encrypt_content` and `_decrypt_content`, for encrypting and decrypting content, respectively. These methods check if an `encryptor` exists; if not, they return the original content. The `read` method first reads data from `AGFS`, then checks if encryption is enabled and if it's a full read (offset is 0 and size is -1). If so, it calls `_decrypt_content` to decrypt. The `write` method first converts the data to bytes, then calls `_encrypt_content` for encryption, and finally writes the encrypted data to `AGFS`.

### 6. APIKeyManager Refactoring

API Keys will be stored as Argon2id hashes instead of plaintext, with a prefix index to optimize verification performance.

**Core Changes**:
- **Added prefix index**: `key_prefix_index` uses the first 8 characters of the API Key for quick candidate lookup.
- **Added Argon2id hasher**: `PasswordHasher`, with parameters: `time_cost=3`, `memory_cost=64MB`, `parallelism=4`.
- **Support for plaintext migration**: Automatically migrates plaintext API Keys to hash format upon loading.

The `APIKeyManager` creates a prefix index dictionary and an Argon2id hasher during initialization. The `load` method, when loading user data, checks for plaintext API Keys and, if found, automatically converts them to a hashed format and saves them, while also building the prefix index. The `resolve` method first checks if it's a Root Key. If not, it extracts the first 8 characters of the API Key as a prefix, uses the prefix index to quickly locate candidate entries, and then verifies each one with the hasher. If verification is successful, it returns the identity information. The `register_user` method generates a random API Key, computes its hash and prefix, saves them to the user data, and updates the prefix index.

### 7. Initialization Process

During service startup, the encryption module is initialized, and the encryptor is injected into VikingFS.

**Core Functionality**:
- **`bootstrap_encryption(config)`**: Creates the `RootKeyProvider` and `FileEncryptor` based on the configuration.
- **`OpenVikingService.initialize()`**: The service initialization process, which includes initializing the encryption module and injecting it into VikingFS.

The `bootstrap_encryption` function first retrieves the encryption configuration. If encryption is not enabled, it returns `None`. If it is enabled, it creates the corresponding `RootKeyProvider` based on the provider type, then creates and returns a `FileEncryptor`. The `initialize` method of `OpenVikingService`, after completing other initializations, reads the configuration and calls `bootstrap_encryption` to initialize the encryption module, and then injects the encryptor into `VikingFS` during its initialization.

### 9. Analysis of Encryption's Impact on VikingFS Operations

Since **all encryption and decryption operations are uniformly handled at the VikingFS layer**, AGFS is only responsible for storing ciphertext and does not participate in any cryptographic logic. Based on code analysis, VikingFS operations can be categorized into two types:

#### 9.1 Classification of VikingFS Operations

##### 9.1.1 Operations Unaffected by Encryption (Metadata Operations)

These operations only handle file system metadata, not file content, and are passed directly to AGFS:

| Operation | Description | Impact | Handling Method |
| :--- | :--- | :--- | :--- |
| `mkdir()` | Create directory | ❌ No impact | Pass-through |
| `rm()` | Delete file/directory | ❌ No impact | Pass-through |
| `mv()` | Move file/directory | ❌ No impact | Pass-through |
| `stat()` | Get file information (size, modification time, etc.) | ❌ No impact | Pass-through |
| `exists()` | Check if URI exists | ❌ No impact | Based on `stat` |
| `ls()` | List directory contents | ❌ No impact | Pass-through |
| `tree()` | Recursively list directory contents | ❌ No impact | Implemented at the VikingFS layer |
| `glob()` | File pattern matching | ❌ No impact | Implemented at the VikingFS layer |
| `find()` | Semantic search (vector retrieval) | ❌ No impact | Uses vector index |
| `search()` | Complex search (with session context) | ❌ No impact | Uses vector index |
| `link()` | Create relationship | ❌ No impact | Automatically handled by calling read/write methods |
| `unlink()` | Delete relationship | ❌ No impact | Automatically handled by calling read/write methods |
| `relations()` | Get list of relationships | ❌ No impact | Automatically handled by calling read/write methods |

##### 9.1.2 Operations Affected by Encryption (Content Operations)

These operations read or write file content. **They are handled by VikingFS for encryption/decryption before calling AGFS**:

| Operation | Location | Description | VikingFS Layer Handling |
| :--- | :--- | :--- | :--- |
| `read()` | `viking_fs.py:252` | Basic read | ✅ Read ciphertext from AGFS → Decrypt at VikingFS layer |
| `write()` | `viking_fs.py:276` | Basic write | ✅ Encrypt at VikingFS layer → Write ciphertext to AGFS |
| `grep()` | `viking_fs.py:476` | Content search | ✅ Implemented at VikingFS layer (read → decrypt → search) |
| `abstract()` | `viking_fs.py:738` | Read `.abstract.md` | ✅ Decrypt after reading |
| `overview()` | `viking_fs.py:758` | Read `.overview.md` | ✅ Decrypt after reading |
| `_read_relation_table()` | `viking_fs.py`: internal method | Read `.relations.json` | ✅ Calls `self.read()` for automatic decryption |
| `_write_relation_table()` | `viking_fs.py`: internal method | Write `.relations.json` | ✅ Calls `self.write()` for automatic encryption |
| `move_file()` | `viking_fs.py`: internal method | Move file | ✅ Read and decrypt → Encrypt and write to new location |
| `_recursive_copy_dir_with_encryption()` | `viking_fs.py:444` | Recursively copy directory | ✅ Read and decrypt each file → Encrypt and write to new location |

#### 9.2 Impact Analysis on OpenViking API

| API | Impact | Description |
| :--- | :--- | :--- |
| `read()` / `write()` | ✅ Affected | Automatic encryption/decryption at the VikingFS layer. |
| `grep()` | ✅ Affected | Implemented at the VikingFS layer (read→decrypt→search). |
| `abstract()` | ✅ Affected | Decrypted after reading. |
| `overview()` | ✅ Affected | Decrypted after reading. |
| `find()` / `search()` | ❌ Unaffected | Uses vector index. |
| `mkdir()` / `rm()` / `mv()` | ❌ Unaffected | Metadata operations, passed through directly. |
| `stat()` / `exists()` / `ls()` | ❌ Unaffected | Metadata operations, passed through directly. |
| `tree()` / `glob()` | ❌ Unaffected | Implemented at the VikingFS layer, metadata operations. |
| `link()` / `unlink()` / `relations()` | ❌ Unaffected | Relationship management, automatically handled by read/write methods. |

#### 9.3 Use Cases for `grep`

According to code analysis, `grep()` is primarily used for:

- **Text content search**: Matching file content with regular expressions.
- **Recursive search**: Can search the entire directory tree.
- **Typical use cases**:
  - Finding code/documents containing specific keywords.
  - Log analysis (searching for "error"/"warning").
  - Global search within a codebase.

#### 9.4 VikingFS Layer `grep` Implementation

Since AGFS only sees ciphertext, it cannot perform content searches. Therefore, **the `grep` functionality needs to be re-implemented at the VikingFS layer**.

The core idea is to encrypt all files wholesale, with the `grep` function being fully implemented at the VikingFS layer: read file → decrypt → search.

##### 9.4.1 Core Design Idea

- **Full-scope encryption**: All files are uniformly encrypted, with no exceptions.
- **VikingFS layer `grep`**: Read file content → automatically decrypt → perform regex matching in memory.
- **Recursive search**: Support for recursive directory tree search.
- **Automatic detection**: Automatically determine if a file is encrypted via magic number (to be compatible with old files).

##### 9.4.2 Implementation Details

The `grep` method accepts parameters such as the starting search path, regular expression pattern, whether to search recursively, and file inclusion/exclusion patterns. It first compiles the regular expression, then lists the directory contents. For each entry, if it's a directory and recursive search is enabled, it recursively calls the `grep` method and merges the results. If it's a file, it first checks if it matches the inclusion/exclusion patterns, then reads the file content (which is automatically decrypted), splits the content by lines, searches for matches line by line, and collects and returns the matching results.

### 10. External Object Storage (TOS/SeaweedFS/RustFS) Encryption Design

#### 10.1 Design Principles

**Core Principles**:
1.  **Encryption capability is independent of external object storage**: Our encryption logic is fully implemented at the VikingFS layer, without using native encryption capabilities of external storage like SSE-TOS/SSE-KMS/SSE-C.
2.  **AGFS and object storage only see ciphertext**: All file content is encrypted at the VikingFS layer before being passed to AGFS. AGFS and external object storage are only responsible for storing ciphertext.
3.  **Storage backend agnostic**: We support any S3-compatible storage (e.g., Volcengine TOS, SeaweedFS, RustFS) without requiring special adaptations for different storage systems.
4.  **Unified encryption at the VikingFS layer**: All encryption/decryption operations are performed uniformly at the VikingFS layer to ensure the encryption logic is controllable.

#### 10.2 Unified Encryption at the VikingFS Layer

**Unified Encryption at the VikingFS Layer**

The core idea is to implement all encryption logic uniformly at the VikingFS layer, without relying on the native encryption capabilities of object storage (SSE-TOS/SSE-KMS/SSE-C).

##### 10.2.1 Core Design Principles

- **Unified Encryption Layer**: All encryption/decryption operations are performed at the VikingFS layer. AGFS and object storage only see ciphertext.
- **Storage Backend Agnostic**: The encryption capability does not depend on the features of the external object storage, supporting any S3-compatible storage.
- **Unified Key Management**: Uses the same three-layer key architecture (Root Key → Account Key → File Key).
- **Unified `grep` Capability**: Uses the `grep` implementation at the VikingFS layer.

##### 10.2.2 Architecture Diagram

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│       VikingFS (Encryption Layer)   │
│  - Encrypts/decrypts file content   │
│  - Implements grep at VikingFS layer│
│  - Unified key management           │
└──────┬──────────────────────────────┘
       │
       ▼
┌───────────────────────────────────────────┐
│         AGFS                              │
│  - Passes through ciphertext              │
│  - Does not perform encryption/decryption │
└──────┬────────────────────────────────────┘
       │
       ▼
┌────────────────────────────────────────────────────────┐
│  External Object Storage (TOS/SeaweedFS/RustFS)        │
│  - Stores only ciphertext                              │
│  - Does not rely on its native encryption capabilities │
└────────────────────────────────────────────────────────┘
```

##### 10.2.3 Advantages and Disadvantages

| Dimension | Description |
| :--- | :--- |
| **Advantages** | Storage backend agnostic, fully controllable encryption logic, unified key management, highest security. |
| **Disadvantages** | Does not utilize native encryption capabilities of object storage; `grep` performance depends on the VikingFS layer implementation. |
| **Applicable Scenarios** | General scenarios that require support for multiple storage backends and high control over encryption. |

##### 10.2.4 Configuration Design

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local"
  }
}
```

**Explanation**:
- No need to configure storage-layer encryption parameters.
- Encryption configuration is independent of the storage backend type.
- Unified use of VikingFS layer encryption.

##### 10.2.5 Storage Backend Adaptation

| Storage Backend | Adaptation Method | Explanation |
| :--- | :--- | :--- |
| **Volcengine TOS** | Direct use | Does not use SSE-TOS/SSE-KMS, only as ciphertext storage. |
| **SeaweedFS** | Direct use | Does not use Filer-managed encryption/SSE-C, only as ciphertext storage. |
| **RustFS** | Direct use | Does not use built-in managed/SSE-KMS, only as ciphertext storage. |
| **Other S3-compatible storage** | Direct use | Any S3-compatible storage can be used directly. |

## Functional Test Design

### 1. Impact Assessment

| Module Name | Impact Level | Impact Description | Modification Needed |
| :--- | :--- | :--- | :--- |
| **OpenVikingService** | Low | Needs to initialize encryption components, load configuration | Yes |
| **VikingFS** | High | Core encryption layer, needs to integrate `FileEncryptor`, modify `read`/`write`/`grep` | Yes |
| **AGFS** | None | Acts only as a storage backend, encryption handled at the VikingFS layer | No |
| **LocalFS** | None | Stores ciphertext, does not need to be aware of encryption | No |
| **S3FS** | None | Stores ciphertext, does not need to be aware of encryption | No |
| **MemFS** | None | Stores ciphertext, does not need to be aware of encryption | No |
| **QueueFS** | None | Asynchronous processing queue, not affected by encryption | No |
| **RootKeyProvider** | New | New abstract interface added | Yes |
| **LocalFileProvider** | New | New local key management implementation added | Yes |
| **VaultProvider** | New | New Vault key management implementation added | Yes |
| **VolcengineKMSProvider** | New | New Volcengine KMS implementation added | Yes |
| **FileEncryptor** | New | New file encryptor implementation added | Yes |
| **HTTP API** | None | API interface remains unchanged, encryption handled internally on the server | No |
| **Local Client** | None | Calls the service directly, encryption is transparent | No |
| **HTTP Client** | None | Calls via HTTP, encryption is transparent | No |
| **Rust CLI** | None | Calls via HTTP, encryption is transparent | No |
| **Bot/Agent** | None | Operates via the client, encryption is transparent | No |
| **Session** | None | Session management, not affected by encryption | No |
| **Retrieve** | Medium | `grep` operation needs to be decrypted at the VikingFS layer | Yes (VikingFS only) |
| **Models** | None | Model services, not affected by encryption | No |
| **Parse** | None | File parsing is done in memory, encryption is transparent | No |

**Impact Level Description**:
- **High**: Core encryption logic is in this module, requires significant modifications.
- **Medium**: Some functionality is affected, requires minor modifications.
- **Low**: Only requires initialization configuration, minimal impact.
- **None**: Completely unaffected, no modifications needed.

### 2. Test Scenario Analysis

#### 2.1 Encryption Functionality Test Scenarios
- **Basic Encryption/Decryption**: Verify file encryption and decryption functions.
- **Multi-Tenant Isolation**: Verify encryption isolation between different tenants.
- **Key Management**: Verify the generation, storage, and use of keys.
- **Compatibility**: Verify compatibility with existing unencrypted files.
- **Performance**: Verify the performance impact of encryption.
- **Fault Recovery**: Verify the recovery mechanism after key loss.

#### 2.2 Storage Backend Test Scenarios
- **LocalFS Encryption**: Verify encryption on the local file system.
- **S3 Storage Encryption**: Verify encryption on S3 storage.
- **SeaweedFS Encryption**: Verify encryption on SeaweedFS storage.
- **RustFS Encryption**: Verify encryption on RustFS storage.

#### 2.3 API Interface Test Scenarios
- **HTTP API Encryption**: Verify encryption functionality of the HTTP API.
- **Local Client Encryption**: Verify encryption functionality of the local client.
- **Rust CLI Encryption**: Verify encryption functionality of the Rust command-line tool.
- **Bot/Agent Encryption**: Verify encryption functionality of the smart assistant.

### 3. Test Case Design

#### 3.1 Basic Encryption/Decryption Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-001 | Single File Encryption/Decryption | 1. Create a test file<br>2. Write content<br>3. Read content | The written and read content are identical. |
| TC-002 | Recursive Directory Encryption | 1. Create a test directory structure<br>2. Write multiple files<br>3. Read all files | All file contents are correct. |
| TC-003 | Large File Encryption | 1. Create a large file (>100MB)<br>2. Write content<br>3. Read content | Large file read/write is normal. |
| TC-004 | Empty File Encryption | 1. Create an empty file<br>2. Write (empty content)<br>3. Read | Empty file handling is normal. |

#### 3.2 Multi-Tenant Isolation Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-005 | Tenant A cannot decrypt Tenant B's file | 1. Tenant A creates an encrypted file<br>2. Tenant B attempts to read it | Tenant B's read fails. |
| TC-006 | Tenant Key Isolation | 1. Generate keys for Tenant A and B respectively<br>2. Verify the keys are different | Key isolation is correct. |

#### 3.3 Key Management Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-007 | Local Key File Management | 1. Generate a root key file<br>2. Verify the file format<br>3. Verify key loading | Key management is normal. |
| TC-008 | Volcengine KMS Integration | 1. Configure Volcengine KMS<br>2. Verify key operations | KMS integration is normal. |
| TC-009 | Key Rotation | 1. Perform key rotation<br>2. Verify old files can still be decrypted | Key rotation is successful. |

#### 3.4 Storage Backend Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-010 | LocalFS Encryption | 1. Configure LocalFS<br>2. Perform encryption operations | LocalFS encryption is normal. |
| TC-011 | S3 Storage Encryption | 1. Configure S3 storage<br>2. Perform encryption operations | S3 storage encryption is normal. |
| TC-012 | SeaweedFS Encryption | 1. Configure SeaweedFS<br>2. Perform encryption operations | SeaweedFS encryption is normal. |

#### 3.5 API Interface Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-013 | HTTP API Encryption | 1. Upload a file via HTTP API<br>2. Verify encrypted storage<br>3. Download via API | API encryption is normal. |
| TC-014 | Local Client Encryption | 1. Operate on files using Local Client<br>2. Verify encryption effect | Local client encryption is normal. |
| TC-015 | Rust CLI Encryption | 1. Operate on files using Rust CLI<br>2. Verify encryption effect | Rust CLI encryption is normal. |

#### 3.6 Disabled Encryption Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-016 | Basic Functions with Encryption Disabled | 1. Configure encryption as disabled<br>2. Create a test file<br>3. Write and read content | File reads and writes normally, no encryption. |
| TC-017 | Directory Operations with Encryption Disabled | 1. Configure encryption as disabled<br>2. Create a directory structure<br>3. Perform ls/rm/mv operations | Directory operations are normal. |
| TC-018 | `grep` Function with Encryption Disabled | 1. Configure encryption as disabled<br>2. Write a test file<br>3. Perform a `grep` search | `grep` function is normal. |
| TC-019 | `add-resource` with Encryption Disabled | 1. Configure encryption as disabled<br>2. Add a resource file<br>3. Verify resource processing | Resource processing is normal. |
| TC-020 | Multi-Tenancy with Encryption Disabled | 1. Configure encryption as disabled<br>2. Multi-tenant file operations<br>3. Verify tenant isolation | Tenant isolation is normal. |

#### 3.7 Encryption Toggle Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-021 | From Disabled to Enabled | 1. First, create a file with encryption disabled<br>2. Enable encryption<br>3. Read the old file<br>4. Write a new file | Old file is read normally, new file is encrypted. |
| TC-022 | From Enabled to Disabled | 1. First, create a file with encryption enabled<br>2. Disable encryption<br>3. Attempt to read the encrypted file | Reading the encrypted file fails (no decryption capability). |

#### 3.8 Backward Compatibility Tests

| Test Case ID | Test Name | Test Steps | Expected Result |
| :--- | :--- | :--- | :--- |
| TC-023 | Data Migration from Non-Encrypted Version | 1. Use an old version (without encryption) to create data<br>2. Upgrade to the encrypted version<br>3. Verify data can be read | Old data is read normally. |
| TC-024 | Encrypted Version Data Compatibility | 1. Create data in the encrypted version<br>2. Roll back to the non-encrypted version<br>3. Attempt to read the encrypted data | Encrypted data cannot be read (expected behavior). |
| TC-025 | Configuration File Compatibility | 1. Use an old configuration file (without encryption settings)<br>2. Start the encrypted version<br>3. Verify the default behavior | Encryption is disabled by default when there is no encryption configuration. |

## Non-Functional Design

### High Availability Design

1.  **Root Key Backup**:
    -   **Local Provider**: The Root Key file must be backed up to a secure location (offline storage, password manager, etc.).
    -   **Vault/AWS KMS Provider**: Relies on the high availability and backup of the cloud service provider.

2.  **Degradation Mode**:
    -   In case of an encryption module failure, the system can be downgraded to plaintext mode by setting `encryption.enabled=false` (for emergency recovery only).

### Compatibility Design

1.  **Backward Compatibility**:
    -   Unencrypted files can still be read normally.
    -   Plaintext API keys are automatically migrated to a hashed format.

2.  **Forward Compatibility**:
    -   The encrypted file format includes a version number to support future upgrades.
    -   A provider type identifier is included to support switching providers.

### Upgrade Plan Design

1.  **Incremental Upgrade**:
    -   Newly written files are automatically encrypted.
    -   Old files remain in plaintext and can be gradually encrypted through a background task.

### Usability Design

1.  **Zero-Configuration Default**:
    -   **Development Environment**: Automatically generates a Local Provider Root Key.
    -   **Production Environment**: Provides clear configuration documentation.

### Fault Recovery Plan Design

1.  **Root Key Loss**:
    -   **Local Provider**: Unrecoverable; must be restored from a backup.
    -   **Vault/AWS KMS Provider**: Relies on the key recovery of the cloud service provider.

2.  **Data Corruption**:
    -   AES-GCM provides integrity checks; corrupted files will throw an `InvalidTag` exception.
    -   It is recommended to back up AGFS data regularly.

## Related Source File Locations

### New Files

| File Path | Description |
| :--- | :--- |
| `openviking/crypto/__init__.py` | Entry point for the encryption module |
| `openviking/crypto/providers.py` | Root Key Provider implementations (including LocalFileProvider, VaultProvider, VolcengineKMSProvider) |
| `openviking/crypto/encryptor.py` | File encryptor (Envelope Encryption) |
| `openviking/crypto/config.py` | Encryption configuration and initialization (`bootstrap_encryption`) |
| `openviking/crypto/exceptions.py` | Definitions of encryption-related exceptions |

### Modified Files

| File Path | Modification Content |
| :--- | :--- |
| `openviking/storage/viking_fs.py` | Integrate `FileEncryptor`, perform encryption/decryption in `read`/`write`/`abstract`/`overview`, implement VikingFS layer `grep` |
| `openviking/server/api_keys.py` | Change API Key storage from plaintext to Argon2id hash |
| `openviking/service/core.py` | Call `bootstrap_encryption` during service initialization and inject the `encryptor` into VikingFS |

--- 
# OpenViking多租户加密技术实现方案

## 概述

本方案针对 OpenViking 商业版多租户架构设计，实现静态数据加密保护。核心采用 Server 端 Root Key 派生 + Envelope Encryption 架构，**所有加解密操作统一在 VikingFS 层完成**，AGFS 和外部对象存储只看到密文，确保加密逻辑可控且与存储后端解耦。

方案包含：
- 三层密钥架构（Root Key → Account Key → File Key）
- 多种 Root Key Provider（本地文件、HashiCorp Vault、Volcengine KMS）
- API Key 哈希保护
- VikingFS 层统一加解密，实现对 AGFS 文件内容的透明加解密
- VikingFS 层实现 grep，绕过 AGFS 的密文搜索问题
- 外部对象存储加密不依赖存储后端原生能力

## 背景

### 现状问题

OpenViking 是多租户系统，不同客户（account）的数据（资源文件、记忆、技能）都存储在同一套服务端 AGFS 中。当前所有敏感数据均以明文存储：

| 数据类型            | 当前存储位置                              | 当前状态        |
| ------------------- | ----------------------------------------- | --------------- |
| 用户 API Key        | `/{account_id}/_system/users.json` (AGFS) | 明文  ` secrets.token_hex(32)` |
| 文件内容 (L0/L1/L2) | `/local/{account_id}/...` (AGFS)          | 明文 UTF-8 文本 |
| 关系数据            | `.relations.json` (AGFS)                  | 明文 JSON       |
| 系统账户表          | `/_system/accounts.json` (AGFS)           | 明文 JSON       |

### 威胁

**核心威胁**：有服务端存储访问权限的人（运维人员、DBA、或存储系统被入侵时的攻击者）可以直接读取任意客户的明文数据。

**防护目标**：即使攻击者拿到了 AGFS 磁盘上的全部文件，在没有对应 account 的加密密钥的情况下，无法读取任何客户的文件内容。

### 加密范围与状态

| 数据               | 是否加密 | 方式                     | 状态     |
| ------------------ | -------- | ------------------------ | -------- |
| AGFS 文件内容      | ✅        | AES-256-GCM 对称加密     | 待实现 |
| API Key            | ✅        | Argon2id 单向哈希        | 待实现 |
| VectorDB           | ❌        | 由 VectorDB 后端自身负责 | 已有设计 |
| `ov.conf` 配置文件 | ❌        | 不加密                   | 已有设计 |

## 目标和非目标

### 目标

1. **静态数据加密**：所有 AGFS 存储的文件内容（L0/L1/L2、relations、users.json、accounts.json）均以加密形式存储，**所有加解密在 VikingFS 层完成**
2. **多租户隔离**：不同账户的数据使用独立的加密密钥，确保跨账户数据隔离
3. **零 API 侵入**：对现有 OpenViking API 完全兼容，无需客户端修改
4. **可插拔架构**：支持多种 Root Key 存储方式（本地文件、HashiCorp Vault、AWS KMS）
5. **向后兼容**：支持未加密文件的读取，实现平滑迁移
6. **性能可接受**：加密/解密开销在可接受范围内（AES-256-GCM 硬件加速 &gt;1GB/s）
7. **存储后端无关**：加密能力不依赖外部对象存储（TOS/SeaweedFS/RustFS）的原生加密能力

### 非目标

1. **传输层加密**：本方案不涉及 TLS/HTTPS 等传输层加密（假设已由部署环境提供）
2. **客户端加密**：本方案不实现客户端端到端加密（E2EE），加密在服务端 VikingFS 层完成
3. **VectorDB 加密**：VectorDB 的加密由其自身后端负责，不在本方案范围内
4. **密钥轮换**：本方案 MVP 版本不实现自动密钥轮换（仅预留接口）
5. **AGFS 层加密**：本方案不在 AGFS 层实现加密，所有加密逻辑在 VikingFS 层完成
6. **外部对象存储原生加密**：本方案不使用 SSE-TOS/SSE-KMS/SSE-C 等外部存储原生加密能力

## 系统指标

### 功能指标

| 指标            | 描述                         | 验收标准                                               |
| --------------- | ---------------------------- | ------------------------------------------------------ |
| 文件加密        | AGFS 写入的文件自动加密      | 写入文件后，磁盘上的文件以 `OVE1` 魔数开头，内容为密文 |
| 文件解密        | AGFS 读取的文件自动解密      | 读取加密文件能正确还原为明文                           |
| 多租户隔离      | 不同账户使用独立密钥         | 账户 A 的密钥无法解密账户 B 的文件                     |
| API Key 哈希    | API Key 以 Argon2id 哈希存储 | users.json 中不再存储明文 API Key                      |
| 向后兼容        | 未加密文件仍可读取           | 读取旧版未加密文件能正确返回明文                       |
| 可插拔 Provider | 支持多种 Root Key 存储       | 可通过配置切换 Local/Vault/AWS KMS Provider            |

### 性能指标

| 指标         | 描述                 | 验收标准                     |
| ------------ | -------------------- | ---------------------------- |
| 加密吞吐量   | AES-256-GCM 加密性能 | ≥ 500 MB/s（硬件加速）       |
| 解密吞吐量   | AES-256-GCM 解密性能 | ≥ 500 MB/s（硬件加速）       |
| API Key 验证 | Argon2id 验证耗时    | ≤ 100ms/次                   |
| 密钥派生     | HKDF-SHA256 派生耗时 | ≤ 1ms/次                     |
| 文件读写延迟 | 加密对读写延迟的影响 | 增加延迟 ≤ 10%（相比未加密） |

## 术语解释

本方案涉及的密码学和云安全相关术语解释如下：

| 术语                    | 英文全称                                                     | 解释                                                         |
| ----------------------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| **AES-256-GCM**         | Advanced Encryption Standard 256-bit with Galois/Counter Mode | NIST 标准的对称加密算法，256位密钥，同时提供加密和完整性认证（AEAD），支持 CPU 硬件加速 |
| **AEAD**                | Authenticated Encryption with Associated Data                | 带关联数据的认证加密，在加密的同时提供完整性校验，防止数据被篡改 |
| **HKDF**                | HMAC-based Extract-and-Expand Key Derivation Function        | RFC 5869 标准的密钥派生函数，用于从高熵主密钥派生出多个子密钥 |
| **KMS**                 | Key Management Service                                       | 密钥管理服务，用于安全地生成、存储、轮换和销毁加密密钥，通常由云服务商提供（如 AWS KMS、阿里云 KMS） |
| **HashiCorp Vault**     | HashiCorp Vault                                              | 开源的密钥管理和机密存储系统，提供 Transit Engine 支持"加密即服务" |
| **Transit Engine**      | Vault Transit Engine                                         | HashiCorp Vault 的加密引擎，Root Key 永不出 Vault，支持密钥版本管理和自动轮转 |
| **Envelope Encryption** | 信封加密                                                     | 一种分层加密架构：用 Data Encryption Key (DEK) 加密数据，用 Key Encryption Key (KEK) 加密 DEK，KEK 由 KMS 托管 |
| **DEK**                 | Data Encryption Key                                          | 数据加密密钥，用于加密实际文件内容，每次写入生成新的随机密钥 |
| **KEK**                 | Key Encryption Key                                           | 密钥加密密钥，用于加密 DEK，由 Root Key 派生或由 KMS 管理    |
| **Root Key**            | 根密钥                                                       | 整个加密系统的主密钥，用于派生所有 Account Key，必须安全保管 |
| **Account Key**         | 账户密钥                                                     | 每个租户独立的加密密钥，由 Root Key 通过 HKDF 派生，用于加密该账户下的所有 File Key |
| **File Key**            | 文件密钥                                                     | 每个文件独立的一次性随机密钥，用于加密文件内容               |
| **Argon2id**            | Argon2id                                                     | 2015 年密码哈希竞赛（PHC）冠军算法，专为抵抗 GPU/ASIC 暴力破解设计，混合了 Argon2i（抗侧信道）和 Argon2d（抗 GPU）的优势 |
| **IV**                  | Initialization Vector                                        | 初始化向量，用于对称加密算法，确保相同明文在相同密钥下产生不同密文，防止模式分析 |
| **GCM Tag**             | Galois/Counter Mode Authentication Tag                       | GCM 模式生成的认证标签，用于验证数据完整性和真实性，被篡改的数据会导致标签验证失败 |
| **魔数**                | Magic Number                                                 | 文件开头的固定字节序列，用于标识文件格式（本方案使用 `b"OVE1"` 表示 OpenViking Encryption v1） |
| **SSE**                 | Server-Side Encryption                                       | 服务端加密，对象存储（如 S3/TOS）提供的原生加密能力          |
| **SSE-KMS**             | Server-Side Encryption with KMS                              | 使用 KMS 管理密钥的服务端加密，支持每租户独立密钥            |
| **SSE-C**               | Server-Side Encryption with Customer-Provided Keys           | 客户提供密钥的服务端加密，密钥不在云服务端持久化存储         |

## 功能架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        OpenViking Server                        │
├─────────────────────────────────────────────────────────────────┤
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                     API Layer (无变更)                     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                    VikingFS (加密层)                       │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  FileEncryptor (AES-256-GCM + Envelope)             │  │  │
│  │  │  - encrypt(account_id, plaintext) → ciphertext      │  │  │
│  │  │  - decrypt(account_id, ciphertext) → plaintext      │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                   KeyManager (密钥管理)                    │  │
│  │  ┌─────────────────────────────────────────────────────┐  │  │
│  │  │  RootKeyProvider (抽象接口)                          │  │  │
│  │  │  ├─ LocalFileProvider (开发/单节点)                  │  │  │
│  │  │  ├─ VaultProvider (生产/多云)                        │  │  │
│  │  │  └─ VolcengineKMSProvider (火山引擎云)                │  │  │
│  │  └─────────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                            │                                    │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │                      AGFS (存储层)                         │  │
│  │  (LocalFS / s3FS / memFS，无变更)                          │  │
│  └───────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 接口边界图

### 新增模块接口

```
┌─────────────────────────────────────────────────────────────────┐
│                        新增模块接口                               │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.RootKeyProvider (抽象基类)              │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + async get_root_key() -&gt; bytes                       │  │
│  │  + async derive_account_key(account_id: str) -&gt; bytes  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                           ▲                                     │
│          ┌────────────────┼────────────────┐                    │
│          │                │                │                    │
│  ┌───────┴──────┐ ┌───────┴──────┐ ┌─────────────────────┐      │
│  │LocalProvider │ │VaultProvider │ │VolcengineKMSProvider│      │
│  └──────────────┘ └──────────────┘ └─────────────────────┘      │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.FileEncryptor                          │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + async encrypt(account_id: str, plaintext: bytes)       │  │
│  │                    -&gt; bytes                            │  │
│  │  + async decrypt(account_id: str, ciphertext: bytes)      │  │
│  │                    -&gt; bytes                            │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  openviking.crypto.APIKeyHasher                           │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  + hash(api_key: str) -&gt; str                           │  │
│  │  + verify(stored_hash: str, api_key: str) -&gt; bool      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 现有模块修改接口

```
┌─────────────────────────────────────────────────────────────────┐
│                      现有模块修改接口                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  VikingFS (修改)                                           │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  __init__(..., encryptor: Optional[FileEncryptor] = None) │  │
│  │  read(..., ctx) → 解密（如果是加密文件）                      │  │
│  │  write(..., ctx) → 加密（如果启用）                          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  APIKeyManager (修改)                                      │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  load() → 读取哈希后的 API Key                              │  │
│  │  resolve(api_key) → Argon2id 验证                          │  │
│  │  register_user() → 存储哈希                                │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 技术架构图

### 三层密钥架构

```
┌─────────────────────────────────────────────────────────────────┐
│                    三层密钥架构 (Envelope Encryption)             │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Layer 1: Root Key (根密钥)                                │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  • 映射：整个 OpenViking 实例（全局唯一）                     │  │
│  │  • 存储：KMS 服务 / ~/.openviking/master.key               │  │
│  │  • 管理者：系统管理员（ROOT 角色）                            │  │
│  │  • 作用：派生所有 Account Key                               │  │
│  └───────────────────────────────────────────────────────────┘  │
│                         │ HKDF-SHA256(Root Key, account_id)     │
│                         ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Layer 2: Account Key (账户密钥，KEK)                       │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  • 映射：一个账户（APIKeyManager 中的一个 workspace）         │  │
│  │  • 数量：每个 account_id 一个                               │  │
│  │  • 存储：不存储，运行时派生                                   │  │
│  │  • 隔离：teamA 的密钥无法解密 teamB 的文件                    │  │
│  │  • 作用：加密该账户下的所有 File Key                          │  │
│  └───────────────────────────────────────────────────────────┘  │
│                         │ AES-256-GCM(account key, file key)    │
│                         ▼                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Layer 3: File Key (文件密钥，DEK)                          │  │
│  ├───────────────────────────────────────────────────────────┤   │
│  │  • 映射：AGFS 中的单个文件（L0/L1/L2、relations、users）       │  │
│  │  • 数量：每次 VikingFS.write() 调用生成一个新的                │  │
│  │  • 存储：加密后存储在文件头（Envelope）                        │  │
│  │  • 作用：加密实际文件内容                                     │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 数据流转图

#### 写入流程

```
Client                    VikingFS                FileEncryptor          KeyManager           AGFS
  │                          │                         │                     │                │
  │  write(uri, content)     │                         │                     │                │
  │─────────────────────────&gt;│                      │                     │                │
  │                          │  encrypt(account_id,    │                     │                │
  │                          │           plaintext)    │                     │                │
  │                          │─────────────────────&gt;│                     │                │
  │                          │                         │ derive_account_key()│                │
  │                          │                         │─────────────────&gt;│                │
  │                          │                         │&lt;─────────────────│                │
  │                          │                         │  account_key        │                │
  │                          │                         │                     │                │
  │                          │  1. Generate file_key   │                     │                │
  │                          │  2. Encrypt content     │                     │                │
  │                          │     with file_key       │                     │                │
  │                          │  3. Encrypt file_key    │                     │                │
  │                          │     with account_key    │                     │                │
  │                          │  4. Build envelope      │                     │                │
  │                          │&lt;─────────────────────│                     │                │
  │                          │  ciphertext             │                     │                │
  │                          │────────────────────────────────────────────────────────────&gt;│
  │                          │                         │                     │                │ Write
  │&lt;──────────────────────│                         │                     │                │
  │   success                │                         │                     │                │
```

#### 读取流程

```
Client                    VikingFS                FileEncryptor          KeyManager           AGFS
  │                          │                         │                     │                │
  │  read(uri)               │                         │                     │                │
  │──────────────────────&gt;│                         │                     │                │
  │                          │────────────────────────────────────────────────────────────&gt;│
  │                          │                         │                     │                │ Read
  │                          │&lt;────────────────────────────────────────────────────────────│
  │                          │  raw_bytes              │                     │                │
  │                          │  Check magic == "OVE1"? │                     │                │
  │                          │  Yes → decrypt()        │                     │                │
  │                          │─────────────────────&gt;│                     │                │
  │                          │                         │ derive_account_key()│                │
  │                          │                         │───────── ───────&gt;│                │
  │                          │                         │&lt;─────────────────│                │
  │                          │                         │  account_key        │                │
  │                          │                         │                     │                │
  │                          │  1. Parse envelope      │                     │                │
  │                          │  2. Decrypt file_key    │                     │                │
  │                          │     with account_key    │                     │                │
  │                          │  3. Decrypt content     │                     │                │
  │                          │     with file_key       │                     │                │
  │                          │&lt;─────────────────────│                     │                │
  │                          │  plaintext              │                     │                │
  │&lt;────────────────── ───│                         │                     │                │
  │   content                │                         │                     │                │
```

## 数据模型设计

### 加密文件格式（Envelope v1）

```
Envelope format:
Offset  Size    Field                   Description
0x00    4       magic                   b"OVE1" (OpenViking Encryption v1)
0x04    1       version                 0x01 (版本号)
0x05    1       provider_type           0x01=local, 0x02=vault, 0x03=volcengine_kms
0x06    2       encrypted_key_length    Big-endian, 加密后的 File Key 长度
0x08    2       key_iv_length           Big-endian, Key IV 长度（Local 模式为 12，其他模式为 0）
0x0A    2       data_iv_length          Big-endian, Data IV 长度（固定为 12）
0x0C    var     encrypted_file_key      加密后的 File Key
var     var     key_iv                  Local 模式：加密 File Key 的 IV；其他模式：空
var     var     data_iv                 加密文件内容的 IV（12 字节）
var     var     encrypted_content       加密后的文件内容（含 16-byte GCM auth tag）
```

**魔数说明**：

- `b"OVE1"`：表示这是 OpenViking Encryption v1 格式的加密文件
- 如果文件不以 `b"OVE1"` 开头，则视为未加密文件，直接返回明文

### API Key 存储格式

**修改前**（明文存储）：

```json
{
  "users": {
    "alice": {
      "role": "admin",
      "key": "abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
    }
  }
}
```

**修改后**（哈希存储）：

```json
{
  "users": {
    "alice": {
      "role": "admin",
      "key_hash": "$argon2id$v=19$m=65536,t=3,p=4$...",
      "key_prefix": "sk-abcde"
    }
  }
}
```

**字段说明**：

- `key_hash`：Argon2id 哈希值（包含算法参数和 salt）
- `key_prefix`：API Key 前 8 个字符，用于 O(1) 快速定位候选条目

### 配置文件格式（ov.conf 新增）

```
# Local file provider
{
  "encryption": {
    "enabled": true,
    "provider": "local",
    "local": {
      "key_file": "~/.openviking/master.key"
    }
  }
}

# Vault provider
{
  "encryption": {
    "enabled": true,
    "provider": "vault",
    "vault": {
      "address": "https://vault.example.com:8200",
      "token": "vault-token-xxx",
      "mount_point": "transit",
      "key_name": "openviking-root"
    }
  }
}

# Volcengine KMS provider
{
  "encryption": {
    "enabled": true,
    "provider": "volcengine_kms",
    "volcengine_kms": {
      "key_id": "kms-key-id-xxx",
      "region": "cn-beijing",
      "access_key": "AKLTxxxxxxxx",
      "secret_key": "Tmpxxxxxxxx"
    }
  }
}
```

### 配置类结构

配置类包含加密配置的各个字段，包括是否启用加密、密钥提供者类型，以及各提供者的具体配置参数。配置类使用 BaseModel 进行类型验证，确保配置的正确性。

## 关键方案设计

### 1. 加密算法选型

#### 对称加密：AES-256-GCM

**选择理由**：

- AEAD（加密+认证一体），一步完成加密和防篡改
- CPU 硬件加速（AES-NI），吞吐量 &gt;1 GB/s
- TLS 1.3 / AWS S3 / GCP 标准算法
- NIST SP 800-38D 标准

**参数**：

- 密钥长度：256-bit
- IV：96-bit（随机生成）
- 认证标签：128-bit

#### 密钥派生：HKDF-SHA256

**选择理由**：

- 专为此场景设计（RFC 5869）
- 微秒级计算开销
- 确定性输出

**参数**：

- 算法：SHA-256
- Salt：固定值 `b"openviking-kek-salt-v1"`
- Info：`f"openviking:kek:v1:{account_id}".encode()`

#### API Key 哈希：Argon2id

**选择理由**：

- RFC 9106 标准，2015 PHC 冠军
- 抗 GPU/ASIC 攻击能力强
- 内存硬度可配置

**参数**：

- 变体：Argon2id（混合 Argon2i + Argon2d）
- 内存：64 MiB
- 迭代次数：3
- 并行度：4

### 2. RootKeyProvider 抽象设计

RootKeyProvider 是整个加密系统的核心抽象，定义了密钥管理的统一接口。所有具体的密钥提供者（LocalFileProvider、VaultProvider、VolcengineKMSProvider）都需要实现这个接口。

**核心方法**：
- `get_root_key()`: 获取 Root Key（仅 Local Provider 使用）
- `derive_account_key(account_id)`: 派生指定账户的 Account Key
- `encrypt_file_key(plaintext_key, account_id)`: 加密 File Key
- `decrypt_file_key(encrypted_key, account_id)`: 解密 File Key

### 3. LocalFileProvider 实现

LocalFileProvider 是最简单的 Root Key Provider，适用于开发环境和单节点部署。Root Key 以十六进制格式存储在本地文件中。

**核心功能**：
- **Root Key 存储**：本地文件（十六进制格式）
- **Account Key 派生**：使用 HKDF-SHA256 从 Root Key 派生
- **File Key 加解密**：使用 AES-256-GCM

#### Local Root Key 文件格式

**文件位置**：`~/.openviking/master.key`（默认值）

**文件格式**：
- 内容：32 字节随机密钥的十六进制编码（64 个字符）
- 文件权限：`chmod 0600`（仅所有者可读可写）

**初始化流程**：
1. 读取密钥文件
2. 验证文件权限（必须是 0600）
3. 解析十六进制内容
4. 验证密钥长度（必须是 32 字节）
5. 缓存密钥到内存

**CLI 命令**：
```bash
ov system crypto init-key --output ~/.openviking/master.key
```
- 如果目标文件已存在，报错并退出
- 生成 32 字节随机密钥
- 以十六进制格式写入文件
- 设置文件权限为 0600

LocalFileProvider 实现了 RootKeyProvider 接口的所有方法。在初始化时，它接收密钥文件路径作为参数。get_root_key 方法负责从文件中读取并缓存根密钥，首次调用时会读取文件，后续调用直接返回缓存的密钥。derive_account_key 方法先获取根密钥，然后使用 HKDF 算法根据账户 ID 派生出账户密钥。encrypt_file_key 方法先派生账户密钥，生成随机 IV，然后使用 AES-GCM 算法加密 File Key，返回加密后的密钥和 IV。decrypt_file_key 方法执行相反的操作，使用账户密钥和 IV 解密 File Key。

### 4. FileEncryptor 实现

FileEncryptor 是文件加密的核心组件，实现了 Envelope Encryption（信封加密）模式。

**核心功能**：
- **Envelope 格式**：Magic (4B) + Provider Type (1B) + Key Length (2B) + Encrypted File Key + Key IV (Local) + Data IV + Encrypted Content
- **魔数检测**：通过 `b"OVE1"` 魔数自动识别加密文件
- **向后兼容**：未加密文件直接返回，支持平滑迁移

FileEncryptor 在初始化时接收一个 RootKeyProvider 实例，并自动检测提供者类型。加密流程如下：首先生成随机的 File Key，然后生成随机的 Data IV，使用 File Key 和 Data IV 通过 AES-GCM 加密文件内容。接下来根据提供者类型加密 File Key：对于 Local 模式，使用提供者的 encrypt_file_key 方法加密 File Key 并获取 key_iv；对于其他模式，只获取加密后的 File Key，key_iv 为空。最后将所有组件组装成 Envelope 格式返回。解密流程首先检查魔数，如果不是加密文件则直接返回。如果是加密文件，解析 Envelope 提取提供者类型、加密的 File Key、key_iv、data_iv 和加密内容。然后根据提供者类型解密 File Key：Local 模式需要传入 key_iv，其他模式不需要。最后使用解密后的 File Key 和 data_iv 解密文件内容。

### 5. VikingFS 集成

**VikingFS 层是唯一的加解密点**，所有文件内容的加解密都在这里完成，AGFS 和外部对象存储只看到密文。

**核心改动**：
- **新增 encryptor 参数**：在 `__init__` 中接收 FileEncryptor
- **read() 自动解密**：读取文件后自动检查并解密（VikingFS 层唯一解密点）
- **write() 自动加密**：写入文件前自动加密（VikingFS 层唯一加密点）
- **grep() VikingFS 层实现**：不依赖 AGFS 的 grep，自己读取、解密、搜索

VikingFS 在初始化时接收 encryptor 参数并保存。提供了两个辅助方法：_encrypt_content 和 _decrypt_content，分别用于加密和解密内容。这两个方法会检查 encryptor 是否存在，如果不存在则直接返回原始内容。read 方法先从 AGFS 读取数据，然后检查是否启用了加密且是完整读取（offset 为 0 且 size 为 -1），如果是则调用 _decrypt_content 进行解密。write 方法先将数据转换为字节，然后调用 _encrypt_content 进行加密，最后将加密后的数据写入 AGFS。

### 6. APIKeyManager 改造

将 API Key 从明文存储改为 Argon2id 哈希存储，增加前缀索引优化验证性能。

**核心改动**：
- **新增前缀索引**：`key_prefix_index`，用 API Key 前 8 字符快速定位候选
- **新增 Argon2id 哈希器**：`PasswordHasher`，参数：time_cost=3, memory_cost=64MB, parallelism=4
- **支持明文迁移**：加载时自动将明文 API Key 迁移为哈希格式

APIKeyManager 在初始化时创建前缀索引字典和 Argon2id 哈希器。load 方法在加载用户数据时，会检查是否存在明文 API Key，如果存在则自动将其转换为哈希格式并保存，同时构建前缀索引。resolve 方法首先检查是否是 Root Key，如果不是则提取 API Key 的前 8 个字符作为前缀，通过前缀索引快速定位候选条目，然后逐一使用哈希器验证，验证成功则返回身份信息。register_user 方法生成随机 API Key，计算其哈希值和前缀，保存到用户数据中，并更新前缀索引。

### 7. 初始化流程

在服务启动时初始化加密模块，并将加密器注入到 VikingFS 中。

**核心功能**：
- **`bootstrap_encryption(config)`**：根据配置创建 RootKeyProvider 和 FileEncryptor
- **`OpenVikingService.initialize()`**：服务初始化流程，完成加密模块初始化和 VikingFS 注入

bootstrap_encryption 函数首先从配置中获取加密配置，如果加密未启用则返回 None。如果启用，则根据提供者类型创建对应的 RootKeyProvider，然后创建 FileEncryptor 并返回。OpenVikingService 的 initialize 方法在完成其他初始化后，读取配置并调用 bootstrap_encryption 初始化加密模块，然后在初始化 VikingFS 时将加密器注入进去。

### 9. VikingFS 操作加密影响分析

由于**所有加解密操作统一在 VikingFS 层完成**，AGFS 只负责存储密文，不参与任何加解密逻辑。根据代码分析，VikingFS 操作可以分为两类：

#### 9.1 VikingFS 操作分类

##### 9.1.1 不受加密影响的操作（元数据操作）

这些操作只处理文件系统元数据，不涉及文件内容，直接透传 AGFS：

| 操作      | 说明                             | 影响     | 处理方式   |
| --------- | -------------------------------- | -------- | ---------- |
| `mkdir()` | 创建目录                         | ❌ 无影响 | 直接透传   |
| `rm()`    | 删除文件/目录                    | ❌ 无影响 | 直接透传   |
| `mv()`    | 移动文件/目录                    | ❌ 无影响 | 直接透传   |
| `stat()`  | 获取文件信息（大小、修改时间等） | ❌ 无影响 | 直接透传   |
| `exists()`| 检查 URI 是否存在                | ❌ 无影响 | 基于 stat  |
| `ls()`    | 列出目录内容                     | ❌ 无影响 | 直接透传   |
| `tree()`  | 递归列出目录内容                 | ❌ 无影响 | VikingFS 层实现 |
| `glob()`  | 文件模式匹配                     | ❌ 无影响 | VikingFS 层实现 |
| `find()`  | 语义搜索（向量检索）             | ❌ 无影响 | 使用向量索引 |
| `search()`| 复杂搜索（带会话上下文）         | ❌ 无影响 | 使用向量索引 |
| `link()`  | 创建关系                         | ❌ 无影响 | 调用读写方法自动处理 |
| `unlink()`| 删除关系                         | ❌ 无影响 | 调用读写方法自动处理 |
| `relations()` | 获取关系列表                  | ❌ 无影响 | 调用读写方法自动处理 |

##### 9.1.2 受加密影响的操作（内容操作）

这些操作会读取或写入文件内容，**在 VikingFS 层完成加解密后，再调用 AGFS**：

| 操作                      | 位置                        | 说明                 | VikingFS 层处理方式                     |
| ------------------------- | --------------------------- | -------------------- | -------------------------------------- |
| `read()`                  | viking_fs.py:252            | 基础读取             | ✅ 从 AGFS 读取密文 → VikingFS 层解密  |
| `write()`                 | viking_fs.py:276            | 基础写入             | ✅ VikingFS 层加密 → 写入 AGFS 密文    |
| `grep()`                  | viking_fs.py:476            | 内容搜索             | ✅ VikingFS 层实现（读取→解密→搜索）   |
| `abstract()`              | viking_fs.py:738            | 读取 .abstract.md    | ✅ 读取后解密                           |
| `overview()`              | viking_fs.py:758            | 读取 .overview.md    | ✅ 读取后解密                           |
| `_read_relation_table()`  | viking_fs.py:内部方法        | 读取 .relations.json | ✅ 调用 self.read() 自动解密            |
| `_write_relation_table()` | viking_fs.py:内部方法        | 写入 .relations.json | ✅ 调用 self.write() 自动加密           |
| `move_file()`             | viking_fs.py:内部方法        | 移动文件             | ✅ 读取解密 → 加密写入新位置            |
| `_recursive_copy_dir_with_encryption()` | viking_fs.py:444 | 递归复制目录 | ✅ 逐文件读取解密 → 加密写入新位置      |

#### 9.2 OpenViking API 影响分析

| API                            | 影响       | 说明                         |
| ------------------------------ | ---------- | ---------------------------- |
| `read()` / `write()`           | ✅ 受影响   | VikingFS 层自动加解密       |
| `grep()`                       | ✅ 受影响   | VikingFS 层实现（读取→解密→搜索） |
| `abstract()`                   | ✅ 受影响   | 读取后解密                   |
| `overview()`                   | ✅ 受影响   | 读取后解密                   |
| `find()` / `search()`          | ❌ 不受影响 | 使用向量索引                 |
| `mkdir()` / `rm()` / `mv()`    | ❌ 不受影响 | 元数据操作，直接透传         |
| `stat()` / `exists()` / `ls()` | ❌ 不受影响 | 元数据操作，直接透传         |
| `tree()` / `glob()`             | ❌ 不受影响 | VikingFS 层实现，元数据操作  |
| `link()` / `unlink()` / `relations()` | ❌ 不受影响 | 关系管理，调用读写方法自动处理 |

#### 9.3 grep 的使用场景

根据代码分析，`grep()` 主要用于：

- **文本内容搜索**：按正则表达式匹配文件内容
- **递归搜索**：可搜索整个目录树
- **典型用例**：
  - 查找包含特定关键词的代码/文档
  - 日志分析（搜索 error/warning）
  - 代码库全局搜索

#### 9.4 VikingFS 层 grep 实现

由于 AGFS 看到的是密文，无法进行内容搜索，因此**grep 功能需要在 VikingFS 层重新实现**。

核心思路：所有文件全量加密，grep 功能完全在 VikingFS 层实现，读取文件 → 解密 → 搜索。

##### 9.4.1 核心设计思路

- **全范围加密**：所有文件统一加密，无例外
- **VikingFS 层 grep**：读取文件内容 → 自动解密 → 在内存中进行正则匹配
- **递归搜索**：支持目录树递归搜索
- **自动检测**：通过魔数自动判断是否加密（兼容旧文件）

##### 9.4.2 实现说明

grep 方法接收搜索起始路径、正则表达式模式、是否递归搜索、文件包含/排除模式等参数。首先编译正则表达式，然后列出目录内容。对于每个条目，如果是目录且启用递归搜索，则递归调用 grep 方法并将结果合并。如果是文件，先检查是否匹配包含/排除模式，然后读取文件内容（会自动解密），将内容按行分割，逐行搜索匹配项，收集匹配结果并返回。

### 10. 外部对象存储（TOS/SeaweedFS/RustFS）加密设计

#### 10.1 设计原则

**核心原则**：
1. **加密能力不依赖外部对象存储**：我们的加密逻辑完全在 VikingFS 层实现，不使用 SSE-TOS/SSE-KMS/SSE-C 等外部存储原生加密能力
2. **AGFS 和对象存储只看到密文**：所有文件内容在 VikingFS 层加密后才传给 AGFS，AGFS 和外部对象存储只负责存储密文
3. **存储后端无关**：支持任何 S3 兼容存储（火山引擎 TOS、SeaweedFS、RustFS 等），无需针对不同存储做特殊适配
4. **VikingFS 层统一加密**：所有加密/解密操作统一在 VikingFS 层完成，确保加密逻辑可控

#### 10.2 VikingFS 层统一加密

**VikingFS 层统一加密**

核心思路：所有加密逻辑统一在 VikingFS 层实现，不依赖对象存储的原生加密能力（SSE-TOS/SSE-KMS/SSE-C）。

##### 10.2.1 核心设计思路

- **统一加密层**：所有加密/解密操作在 VikingFS 层完成，AGFS 和对象存储只看到密文
- **存储后端无关**：加密能力不依赖外部对象存储的功能，支持任何 S3 兼容存储
- **密钥管理统一**：使用相同的三层密钥架构（Root Key → Account Key → File Key）
- **grep 能力统一**：使用 VikingFS 层 grep 实现

##### 10.2.2 架构图

```
┌─────────────┐
│   Client    │
└──────┬──────┘
       │
       ▼
┌─────────────────────────────────────┐
│       VikingFS (加密层)              │
│  - 加密/解密文件内容                   │
│  - VikingFS 层 grep 实现             │
│  - 统一密钥管理                       │
└──────┬──────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│         AGFS                        │
│  - 透传密文                          │
│  - 不进行加密/解密                    │
└──────┬──────────────────────────────┘
       │
       ▼
┌─────────────────────────────────────┐
│  外部对象存储 (TOS/SeaweedFS/RustFS)  │
│  - 仅存储密文                         │
│  - 不依赖其原生加密能力                │
└─────────────────────────────────────┘
```

##### 10.2.3 优点与缺点

| 维度 | 说明 |
|------|------|
| **优点** | 存储后端无关，加密逻辑完全可控，密钥统一管理，安全性最高 |
| **缺点** | 不利用对象存储原生加密能力，grep 性能取决于 VikingFS 层实现 |
| **适用场景** | 通用场景，需要支持多种存储后端，对加密控制要求高 |

##### 10.2.4 配置设计

```json
{
  "encryption": {
    "enabled": true,
    "provider": "local"
  }
}
```

**说明**：
- 无需配置存储层加密相关参数
- 加密配置与存储后端类型无关
- 统一使用 VikingFS 层加密

##### 10.2.5 存储后端适配

| 存储后端 | 适配方式 | 说明 |
|----------|----------|------|
| **火山引擎 TOS** | 直接使用 | 不使用 SSE-TOS/SSE-KMS，仅作为密文存储 |
| **SeaweedFS** | 直接使用 | 不使用 Filer 托管加密/SSE-C，仅作为密文存储 |
| **RustFS** | 直接使用 | 不使用内置托管/SSE-KMS，仅作为密文存储 |
| **其他 S3 兼容存储** | 直接使用 | 任何 S3 兼容存储都可直接使用 |

## 功能测试设计

### 1. 影响评估

| 模块名称 | 影响程度 | 影响描述 | 是否需要修改 |
|---------|---------|---------|------------|
| **OpenVikingService** | 低 | 需要初始化加密组件，加载配置 | 是 |
| **VikingFS** | 高 | 核心加密层，需要集成 FileEncryptor，修改 read/write/grep | 是 |
| **AGFS** | 无 | 仅作为存储后端，加密在 VikingFS 层处理 | 否 |
| **LocalFS** | 无 | 存储密文，无需感知加密 | 否 |
| **S3FS** | 无 | 存储密文，无需感知加密 | 否 |
| **MemFS** | 无 | 存储密文，无需感知加密 | 否 |
| **QueueFS** | 无 | 异步处理队列，不受加密影响 | 否 |
| **RootKeyProvider** | 新增 | 新增抽象接口 | 是 |
| **LocalFileProvider** | 新增 | 新增本地密钥管理实现 | 是 |
| **VaultProvider** | 新增 | 新增 Vault 密钥管理实现 | 是 |
| **VolcengineKMSProvider** | 新增 | 新增火山引擎 KMS 实现 | 是 |
| **FileEncryptor** | 新增 | 新增文件加密器实现 | 是 |
| **HTTP API** | 无 | API 接口不变，加密在服务端内部处理 | 否 |
| **Local Client** | 无 | 直接调用服务，加密透明 | 否 |
| **HTTP Client** | 无 | 通过 HTTP 调用，加密透明 | 否 |
| **Rust CLI** | 无 | 通过 HTTP 调用，加密透明 | 否 |
| **Bot/Agent** | 无 | 通过客户端操作，加密透明 | 否 |
| **Session** | 无 | 会话管理，不受加密影响 | 否 |
| **Retrieve** | 中 | grep 操作需要在 VikingFS 层解密后执行 | 是（仅 VikingFS） |
| **Models** | 无 | 模型服务，不受加密影响 | 否 |
| **Parse** | 无 | 文件解析在内存中进行，加密透明 | 否 |

**影响程度说明**：
- **高**：核心加密逻辑在此模块，需要大量修改
- **中**：部分功能受影响，需要少量修改
- **低**：仅需初始化配置，影响很小
- **无**：完全不受影响，无需修改

### 2. 测试场景分析

#### 2.1 加密功能测试场景
- **基础加密/解密**：验证文件加密和解密功能
- **多租户隔离**：验证不同租户之间的加密隔离
- **密钥管理**：验证密钥的生成、存储和使用
- **兼容性**：验证对现有未加密文件的兼容
- **性能**：验证加密对性能的影响
- **故障恢复**：验证密钥丢失后的恢复机制

#### 2.2 存储后端测试场景
- **LocalFS 加密**：验证本地文件系统加密
- **S3 存储加密**：验证 S3 存储的加密
- **SeaweedFS 加密**：验证 SeaweedFS 存储的加密
- **RustFS 加密**：验证 RustFS 存储的加密

#### 2.3 API 接口测试场景
- **HTTP API 加密**：验证 HTTP API 的加密功能
- **Local Client 加密**：验证本地客户端的加密功能
- **Rust CLI 加密**：验证 Rust 命令行工具的加密功能
- **Bot/Agent 加密**：验证智能助手的加密功能

### 3. 测试用例设计

#### 3.1 基础加密/解密测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-001 | 单文件加密/解密 | 1. 创建测试文件&lt;br&gt;2. 写入内容&lt;br&gt;3. 读取内容 | 写入和读取的内容一致 |
| TC-002 | 目录递归加密 | 1. 创建测试目录结构&lt;br&gt;2. 写入多个文件&lt;br&gt;3. 读取所有文件 | 所有文件内容正确 |
| TC-003 | 大文件加密 | 1. 创建大文件（&gt;100MB）&lt;br&gt;2. 写入内容&lt;br&gt;3. 读取内容 | 大文件读写正常 |
| TC-004 | 空文件加密 | 1. 创建空文件&lt;br&gt;2. 写入（空内容）&lt;br&gt;3. 读取 | 空文件处理正常 |

#### 3.2 多租户隔离测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-005 | 租户 A 无法解密租户 B 的文件 | 1. 租户 A 创建加密文件&lt;br&gt;2. 租户 B 尝试读取 | 租户 B 读取失败 |
| TC-006 | 租户密钥隔离 | 1. 分别为租户 A 和 B 生成密钥&lt;br&gt;2. 验证密钥不同 | 密钥隔离正确 |

#### 3.3 密钥管理测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-007 | 本地密钥文件管理 | 1. 生成根密钥文件&lt;br&gt;2. 验证文件格式&lt;br&gt;3. 验证密钥加载 | 密钥管理正常 |
| TC-008 | 火山引擎 KMS 集成 | 1. 配置火山引擎 KMS&lt;br&gt;2. 验证密钥操作 | KMS 集成正常 |
| TC-009 | 密钥轮换 | 1. 执行密钥轮换&lt;br&gt;2. 验证旧文件仍可解密 | 密钥轮换成功 |

#### 3.4 存储后端测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-010 | LocalFS 加密 | 1. 配置 LocalFS&lt;br&gt;2. 执行加密操作 | LocalFS 加密正常 |
| TC-011 | S3 存储加密 | 1. 配置 S3 存储&lt;br&gt;2. 执行加密操作 | S3 存储加密正常 |
| TC-012 | SeaweedFS 加密 | 1. 配置 SeaweedFS&lt;br&gt;2. 执行加密操作 | SeaweedFS 加密正常 |

#### 3.5 API 接口测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-013 | HTTP API 加密 | 1. 通过 HTTP API 上传文件&lt;br&gt;2. 验证加密存储&lt;br&gt;3. 通过 API 下载 | API 加密正常 |
| TC-014 | Local Client 加密 | 1. 使用 Local Client 操作文件&lt;br&gt;2. 验证加密效果 | 本地客户端加密正常 |
| TC-015 | Rust CLI 加密 | 1. 使用 Rust CLI 操作文件&lt;br&gt;2. 验证加密效果 | Rust CLI 加密正常 |

#### 3.6 关闭加密测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-016 | 关闭加密基础功能 | 1. 配置加密 disabled&lt;br&gt;2. 创建测试文件&lt;br&gt;3. 写入和读取内容 | 文件正常读写，无加密 |
| TC-017 | 关闭加密目录操作 | 1. 配置加密 disabled&lt;br&gt;2. 创建目录结构&lt;br&gt;3. 执行 ls/rm/mv 操作 | 目录操作正常 |
| TC-018 | 关闭加密 grep 功能 | 1. 配置加密 disabled&lt;br&gt;2. 写入测试文件&lt;br&gt;3. 执行 grep 搜索 | grep 功能正常 |
| TC-019 | 关闭加密 add-resource | 1. 配置加密 disabled&lt;br&gt;2. 添加资源文件&lt;br&gt;3. 验证资源处理 | 资源处理正常 |
| TC-020 | 关闭加密多租户 | 1. 配置加密 disabled&lt;br&gt;2. 多租户操作文件&lt;br&gt;3. 验证租户隔离 | 租户隔离正常 |

#### 3.7 加密开关切换测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-021 | 从关闭到开启 | 1. 先关闭加密创建文件&lt;br&gt;2. 开启加密&lt;br&gt;3. 读取旧文件&lt;br&gt;4. 写入新文件 | 旧文件正常读取，新文件加密 |
| TC-022 | 从开启到关闭 | 1. 先开启加密创建文件&lt;br&gt;2. 关闭加密&lt;br&gt;3. 尝试读取加密文件 | 加密文件读取失败（无解密能力） |

#### 3.8 向后兼容性测试

| 测试用例 ID | 测试名称 | 测试步骤 | 预期结果 |
|------------|----------|----------|----------|
| TC-023 | 无加密版本数据迁移 | 1. 使用旧版本（无加密）创建数据&lt;br&gt;2. 升级到加密版本&lt;br&gt;3. 验证数据读取 | 旧数据正常读取 |
| TC-024 | 加密版本数据兼容性 | 1. 在加密版本创建数据&lt;br&gt;2. 回退到无加密版本&lt;br&gt;3. 尝试读取加密数据 | 加密数据无法读取（预期行为） |
| TC-025 | 配置文件兼容性 | 1. 使用旧配置文件（无加密配置）&lt;br&gt;2. 启动加密版本&lt;br&gt;3. 验证默认行为 | 无加密配置时默认关闭加密 |

## 非功能设计

### 高可用设计

1. **Root Key 备份**：
   - Local Provider：Root Key 文件必须备份到安全位置（离线存储、密码管理器等）
   - Vault/AWS KMS Provider：依赖云服务商的高可用和备份

2. **降级模式**：
   - 加密模块故障时，可通过配置 `encryption.enabled=false` 降级为明文模式（仅用于紧急恢复）

### 兼容性设计

1. **向后兼容**：
   - 未加密文件仍可正常读取
   - 明文 API Key 自动迁移到哈希格式

2. **向前兼容**：
   - 加密文件格式包含版本号，支持未来升级
   - Provider 类型标识，支持切换 Provider

### 升级方案设计

1. **增量升级**：
   - 新写入的文件自动加密
   - 旧文件保持明文，可通过后台任务逐步加密

### 易用性设计

1. **零配置默认**：
   - 开发环境：自动生成 Local Provider Root Key
   - 生产环境：提供清晰的配置文档

### 故障恢复方案设计

1. **Root Key 丢失**：
   - Local Provider：无法恢复，必须从备份恢复
   - Vault/AWS KMS Provider：依赖云服务商的密钥恢复

2. **数据损坏**：
   - AES-GCM 提供完整性校验，损坏的文件会抛出 `InvalidTag` 异常
   - 建议定期备份 AGFS 数据

## 相关源文件地址

### 新增文件

| 文件路径                              | 描述                              |
| ------------------------------------- | --------------------------------- |
| `openviking/crypto/__init__.py`       | 加密模块入口                      |
| `openviking/crypto/providers.py`       | Root Key Provider 实现（包含 LocalFileProvider、VaultProvider、VolcengineKMSProvider） |
| `openviking/crypto/encryptor.py`      | 文件加密器（Envelope Encryption） |
| `openviking/crypto/config.py`          | 加密配置与初始化（bootstrap_encryption） |
| `openviking/crypto/exceptions.py`      | 加密相关异常定义                  |

### 修改文件

| 文件路径                                | 修改内容                                   |
| --------------------------------------- | ------------------------------------------ |
| `openviking/storage/viking_fs.py`       | 集成 FileEncryptor，在 read/write/abstract/overview 中加解密，实现 VikingFS 层 grep |
| `openviking/server/api_keys.py`         | API Key 明文存储改为 Argon2id 哈希         |
| `openviking/service/core.py`            | 服务初始化时调用 bootstrap_encryption 并将 encryptor 注入 VikingFS |
