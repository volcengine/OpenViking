//! Lock token encoder/decoder.
//!
//! Wire format: `{owner_id}:{time_ns}:{lock_type}` where lock_type is `E` or `T`.

use super::types::{LockToken, PathLockError, PathLockKind, PathLockResult};

/// Encodes and decodes lock tokens in the `owner_id:time_ns:lock_type` text format.
pub struct LockTokenCodec;

impl LockTokenCodec {
    /// Encode a `LockToken` into its wire format.
    pub fn encode(token: &LockToken) -> String {
        format!(
            "{}:{}:{}",
            token.owner_id,
            token.time_ns,
            token.lock_type.to_code()
        )
    }

    /// Decode a raw token string into a `LockToken`.
    /// Only accepts `E` (Exact) and `T` (Tree) lock type codes.
    pub fn decode(raw: &str) -> PathLockResult<LockToken> {
        let (owner_id, rest) = raw
            .split_once(':')
            .ok_or_else(|| PathLockError::InvalidToken(format!("missing ':' in token: {raw}")))?;

        let (time_ns_str, rest) = rest.split_once(':').ok_or_else(|| {
            PathLockError::InvalidToken(format!("missing second ':' in token: {raw}"))
        })?;

        let time_ns: u128 = time_ns_str.parse().map_err(|_| {
            PathLockError::InvalidToken(format!("invalid time_ns in token: {raw}"))
        })?;

        let mut lock_type_chars = rest.chars();
        let lock_type_char = lock_type_chars
            .next()
            .ok_or_else(|| PathLockError::InvalidToken(format!("empty lock type in token: {raw}")))?;
        if lock_type_chars.next().is_some() {
            return Err(PathLockError::InvalidToken(format!(
                "invalid lock type in token: {raw}"
            )));
        }

        let lock_type = PathLockKind::from_code(lock_type_char).ok_or_else(|| {
            PathLockError::InvalidToken(format!(
                "unknown lock type '{}' in token: {raw}",
                lock_type_char
            ))
        })?;

        Ok(LockToken {
            owner_id: owner_id.to_string(),
            time_ns,
            lock_type,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_supported_lock_types() {
        for token in [
            LockToken {
                owner_id: "abc-123".to_string(),
                time_ns: 1700000000000000000,
                lock_type: PathLockKind::Exact,
            },
            LockToken {
                owner_id: "xyz-456".to_string(),
                time_ns: 42,
                lock_type: PathLockKind::Tree,
            },
        ] {
            let encoded = LockTokenCodec::encode(&token);
            let decoded = LockTokenCodec::decode(&encoded).unwrap();
            assert_eq!(decoded.owner_id, token.owner_id);
            assert_eq!(decoded.time_ns, token.time_ns);
            assert_eq!(decoded.lock_type, token.lock_type);
        }
    }

    #[test]
    fn rejects_legacy_and_malformed_tokens() {
        for raw in [
            "owner:100:P",
            "owner:100:S",
            "no-colons",
            "a:b:c:d",
            "owner:100:E:1",
            "a:not-a-number:E",
        ] {
            assert!(matches!(
                LockTokenCodec::decode(raw),
                Err(PathLockError::InvalidToken(_))
            ));
        }
    }
}
