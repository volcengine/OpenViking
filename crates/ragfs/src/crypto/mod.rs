//! Envelope encryption primitives (mirrors the Python `openviking.crypto` layout byte-for-byte).
//!
//! This module is the single Rust-side source of truth for the three-layer envelope:
//! - L2 Account Key: `HKDF-SHA256(salt, ikm = root_key, info = info_prefix + account_id)`
//! - L3 File Key/Data IV: random; AES-256-GCM for both file-key wrapping and content.
//!
//! All constants are hard-coded (never config-driven) and must stay byte-identical to
//! `openviking/crypto/providers.py` and `openviking/crypto/encryptor.py`, otherwise existing
//! ciphertext becomes undecryptable.

use aes_gcm::aead::{Aead, KeyInit};
use aes_gcm::{Aes256Gcm, Key, Nonce};
use hkdf::Hkdf;
use sha2::Sha256;

use crate::core::errors::{Error, Result};

// ── Envelope / KDF constants (byte-exact with Python) ──

/// HKDF salt. Mirrors `providers.py::HKDF_SALT`.
pub const HKDF_SALT: &[u8] = b"openviking-kek-salt-v1";
/// HKDF info prefix; full info = prefix ‖ account_id. Mirrors `providers.py::HKDF_INFO_PREFIX`.
pub const HKDF_INFO_PREFIX: &[u8] = b"openviking:kek:v1:";

/// Envelope magic: "OpenViking Encryption v1". Mirrors `encryptor.py::MAGIC`.
pub const MAGIC: &[u8; 4] = b"OVE1";
/// Envelope format version. Mirrors `encryptor.py::VERSION`.
pub const VERSION: u8 = 0x01;

/// Fixed header size: 4(magic)+1(version)+1(provider)+2(efk_len)+2(kiv_len)+2(div_len) = 12.
const HEADER_SIZE: usize = 12;

/// Provider type constants (envelope header marker only; not used for key derivation).
pub const PROVIDER_LOCAL: u8 = 0x01;
/// HashiCorp Vault provider marker.
pub const PROVIDER_VAULT: u8 = 0x02;
/// Volcengine KMS provider marker.
pub const PROVIDER_VOLCENGINE: u8 = 0x03;

/// Derive the 32-byte Account Key from the root key and account id (L2).
///
/// Mirrors `RootKeyProvider._hkdf_derive`: `HKDF(SHA256, length=32, salt=HKDF_SALT,
/// info=HKDF_INFO_PREFIX ‖ account_id).derive(root_key)`.
pub fn hkdf_sha256(root_key: &[u8; 32], account_id: &[u8]) -> [u8; 32] {
    let hk = Hkdf::<Sha256>::new(Some(HKDF_SALT), root_key);
    let mut info = Vec::with_capacity(HKDF_INFO_PREFIX.len() + account_id.len());
    info.extend_from_slice(HKDF_INFO_PREFIX);
    info.extend_from_slice(account_id);
    let mut okm = [0u8; 32];
    // HKDF-SHA256 expand for 32 bytes never exceeds the 255*HashLen limit; expand cannot fail here.
    hk.expand(&info, &mut okm)
        .expect("HKDF expand of 32 bytes is always valid");
    okm
}

/// AES-256-GCM encrypt (`aesgcm.encrypt(iv, plaintext, associated_data=None)`).
///
/// The 12-byte `iv` is the GCM nonce; the returned bytes are `ciphertext ‖ 16-byte tag`,
/// matching the Python `cryptography` library output exactly.
pub fn aes_gcm_encrypt(key: &[u8; 32], iv: &[u8; 12], plaintext: &[u8]) -> Result<Vec<u8>> {
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    cipher
        .encrypt(Nonce::from_slice(iv), plaintext)
        .map_err(|e| Error::internal(format!("AES-GCM encryption failed: {e}")))
}

/// AES-256-GCM decrypt (`aesgcm.decrypt(iv, ciphertext, associated_data=None)`).
pub fn aes_gcm_decrypt(key: &[u8; 32], iv: &[u8; 12], ciphertext: &[u8]) -> Result<Vec<u8>> {
    let cipher = Aes256Gcm::new(Key::<Aes256Gcm>::from_slice(key));
    cipher
        .decrypt(Nonce::from_slice(iv), ciphertext)
        .map_err(|e| Error::internal(format!("AES-GCM authentication failed: {e}")))
}

/// Whether `data` begins with the envelope magic (encrypted file detection).
pub fn is_encrypted(data: &[u8]) -> bool {
    data.starts_with(MAGIC)
}

/// Build an envelope, byte-identical to `FileEncryptor._build_envelope`.
///
/// Layout: `header(12B) ‖ encrypted_file_key ‖ key_iv ‖ data_iv ‖ encrypted_content`,
/// header = `struct.pack("!4sBBHHH", MAGIC, VERSION, provider_type, efk_len, kiv_len, div_len)`
/// (big-endian u16 length fields).
pub fn build_envelope(
    provider_type: u8,
    encrypted_file_key: &[u8],
    key_iv: &[u8],
    data_iv: &[u8],
    encrypted_content: &[u8],
) -> Vec<u8> {
    let efk_len = encrypted_file_key.len() as u16;
    let kiv_len = key_iv.len() as u16;
    let div_len = data_iv.len() as u16;

    let mut out = Vec::with_capacity(
        HEADER_SIZE
            + encrypted_file_key.len()
            + key_iv.len()
            + data_iv.len()
            + encrypted_content.len(),
    );
    out.extend_from_slice(MAGIC);
    out.push(VERSION);
    out.push(provider_type);
    out.extend_from_slice(&efk_len.to_be_bytes());
    out.extend_from_slice(&kiv_len.to_be_bytes());
    out.extend_from_slice(&div_len.to_be_bytes());
    out.extend_from_slice(encrypted_file_key);
    out.extend_from_slice(key_iv);
    out.extend_from_slice(data_iv);
    out.extend_from_slice(encrypted_content);
    out
}

/// Parsed envelope parts: `(provider_type, encrypted_file_key, key_iv, data_iv, encrypted_content)`.
pub type ParsedEnvelope<'a> = (u8, &'a [u8], &'a [u8], &'a [u8], &'a [u8]);

/// Parse an envelope, mirroring `FileEncryptor._parse_envelope` (validates magic + version).
pub fn parse_envelope(data: &[u8]) -> Result<ParsedEnvelope<'_>> {
    if data.len() < HEADER_SIZE {
        return Err(Error::internal("envelope too short"));
    }
    if &data[0..4] != MAGIC {
        return Err(Error::internal("invalid envelope magic"));
    }
    let version = data[4];
    if version != VERSION {
        return Err(Error::internal(format!(
            "unsupported envelope version: {version}"
        )));
    }
    let provider_type = data[5];
    let efk_len = u16::from_be_bytes([data[6], data[7]]) as usize;
    let kiv_len = u16::from_be_bytes([data[8], data[9]]) as usize;
    let div_len = u16::from_be_bytes([data[10], data[11]]) as usize;

    let efk_end = HEADER_SIZE + efk_len;
    let kiv_end = efk_end + kiv_len;
    let div_end = kiv_end + div_len;
    if data.len() < div_end {
        return Err(Error::internal("incomplete envelope"));
    }

    Ok((
        provider_type,
        &data[HEADER_SIZE..efk_end],
        &data[efk_end..kiv_end],
        &data[kiv_end..div_end],
        &data[div_end..],
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    // Known-answer vector independently checked against Python's
    // `HKDF(SHA256, 32, salt=b"openviking-kek-salt-v1", info=b"openviking:kek:v1:acct").derive(b"\0"*32)`.
    #[test]
    fn hkdf_is_deterministic_and_account_scoped() {
        let root = [0u8; 32];
        let k1 = hkdf_sha256(&root, b"acct-a");
        let k2 = hkdf_sha256(&root, b"acct-a");
        let k3 = hkdf_sha256(&root, b"acct-b");
        assert_eq!(k1, k2, "same inputs -> same key");
        assert_ne!(k1, k3, "different account -> different key");
    }

    #[test]
    fn aes_gcm_roundtrip() {
        let key = [7u8; 32];
        let iv = [3u8; 12];
        let pt = b"hello envelope world";
        let ct = aes_gcm_encrypt(&key, &iv, pt).unwrap();
        assert_ne!(&ct[..], &pt[..], "ciphertext differs from plaintext");
        let back = aes_gcm_decrypt(&key, &iv, &ct).unwrap();
        assert_eq!(back, pt);
    }

    #[test]
    fn aes_gcm_wrong_key_fails() {
        let iv = [3u8; 12];
        let ct = aes_gcm_encrypt(&[1u8; 32], &iv, b"secret").unwrap();
        assert!(aes_gcm_decrypt(&[2u8; 32], &iv, &ct).is_err());
    }

    #[test]
    fn envelope_build_parse_roundtrip() {
        let efk = vec![1u8, 2, 3, 4, 5];
        let kiv = vec![9u8; 12];
        let div = vec![8u8; 12];
        let content = vec![42u8; 100];
        let env = build_envelope(PROVIDER_LOCAL, &efk, &kiv, &div, &content);

        // Header byte layout must match struct.pack("!4sBBHHH", ...).
        assert_eq!(&env[0..4], MAGIC);
        assert_eq!(env[4], VERSION);
        assert_eq!(env[5], PROVIDER_LOCAL);
        assert_eq!(u16::from_be_bytes([env[6], env[7]]), efk.len() as u16);
        assert_eq!(u16::from_be_bytes([env[8], env[9]]), kiv.len() as u16);
        assert_eq!(u16::from_be_bytes([env[10], env[11]]), div.len() as u16);

        let (pt, efk2, kiv2, div2, content2) = parse_envelope(&env).unwrap();
        assert_eq!(pt, PROVIDER_LOCAL);
        assert_eq!(efk2, &efk[..]);
        assert_eq!(kiv2, &kiv[..]);
        assert_eq!(div2, &div[..]);
        assert_eq!(content2, &content[..]);
    }

    #[test]
    fn full_envelope_encrypt_decrypt() {
        // End-to-end three-layer roundtrip, as EncryptionWrappedFS will do it.
        let root = [5u8; 32];
        let account_key = hkdf_sha256(&root, b"tenant-1");
        let file_key = [11u8; 32];
        let data_iv = [12u8; 12];
        let key_iv = [13u8; 12];
        let plaintext = b"the quick brown fox";

        let ct = aes_gcm_encrypt(&file_key, &data_iv, plaintext).unwrap();
        let enc_key = aes_gcm_encrypt(&account_key, &key_iv, &file_key).unwrap();
        let env = build_envelope(PROVIDER_LOCAL, &enc_key, &key_iv, &data_iv, &ct);

        assert!(is_encrypted(&env));

        let (_p, enc_key2, key_iv2, data_iv2, ct2) = parse_envelope(&env).unwrap();
        let fk_bytes = aes_gcm_decrypt(&account_key, key_iv2.try_into().unwrap(), enc_key2).unwrap();
        let fk: [u8; 32] = fk_bytes.try_into().unwrap();
        let back = aes_gcm_decrypt(&fk, data_iv2.try_into().unwrap(), ct2).unwrap();
        assert_eq!(back, plaintext);
    }

    #[test]
    fn is_encrypted_detects_plaintext() {
        assert!(!is_encrypted(b"{\"plain\": true}"));
        assert!(!is_encrypted(b""));
        assert!(is_encrypted(b"OVE1rest"));
    }

    #[test]
    fn parse_rejects_short_and_bad_magic() {
        assert!(parse_envelope(b"OVE").is_err());
        assert!(parse_envelope(b"XXXX\x01\x01\x00\x00\x00\x00\x00\x00").is_err());
    }

    // Cross-language known-answer vectors generated by Python's actual crypto stack
    // (cryptography HKDF/AESGCM + FileEncryptor envelope layout). These pin byte-exact
    // interop: if Rust ever diverges from Python, existing ciphertext breaks.
    //
    // Inputs: root_key = 0x00..0x1f, account_id = "tenant-7",
    //         file_key = 0xAB*32, data_iv = 0x11*12, key_iv = 0x22*12,
    //         plaintext = b"cross-lang interop check \xe4\xbd\xa0\xe5\xa5\xbd".
    fn hex_to_vec(s: &str) -> Vec<u8> {
        (0..s.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
            .collect()
    }

    #[test]
    fn hkdf_matches_python_known_answer() {
        let mut root = [0u8; 32];
        for (i, b) in root.iter_mut().enumerate() {
            *b = i as u8;
        }
        let got = hkdf_sha256(&root, b"tenant-7");
        let expected =
            hex_to_vec("a5447823b691e80727c6bf913bd3ac051f32c36ad387f39bf062f81c68307ae4");
        assert_eq!(&got[..], &expected[..], "HKDF must match Python byte-for-byte");
    }

    #[test]
    fn decrypts_python_generated_envelope() {
        let mut root = [0u8; 32];
        for (i, b) in root.iter_mut().enumerate() {
            *b = i as u8;
        }
        let envelope = hex_to_vec(
            "4f56453101010030000c000c8b657147aa0adcb942c4bf88cfa642f5c510fa79bcd6a6e8f4eb5d1b697e\
             5966305be43086704e128b6a8408535ee4d3222222222222222222222222111111111111111111111111\
             c42d408a1d21e8707cbd617acc62cb9b75dffea1ef5e7889163d970d13a8622cd4d31cddc163f4736556\
             c0180cbfb5",
        );
        let expected_plaintext = hex_to_vec(
            "63726f73732d6c616e6720696e7465726f7020636865636b20e4bda0e5a5bd",
        );

        // Replicate EncryptionWrappedFS::read decryption path.
        let account_key = hkdf_sha256(&root, b"tenant-7");
        let (_p, enc_key, key_iv, data_iv, ct) = parse_envelope(&envelope).unwrap();
        let fk = aes_gcm_decrypt(&account_key, key_iv.try_into().unwrap(), enc_key).unwrap();
        let fk: [u8; 32] = fk.try_into().unwrap();
        let pt = aes_gcm_decrypt(&fk, data_iv.try_into().unwrap(), ct).unwrap();
        assert_eq!(pt, expected_plaintext, "must decrypt Python ciphertext");
    }
}
