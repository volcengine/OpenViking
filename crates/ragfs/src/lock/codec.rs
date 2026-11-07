//! Lock token encoder/decoder.
//!
//! Holder format: `v2|owner|holder:kind:time,...`. Legacy owner tokens remain readable.

use super::types::{LockHolder, LockToken, PathLockError, PathLockKind, PathLockResult};

/// Encodes holder tokens and decodes holder or legacy token formats.
pub struct LockTokenCodec;

impl LockTokenCodec {
    /// Encode a `LockToken` into its wire format.
    pub fn encode(token: &LockToken) -> String {
        if !token.holders.is_empty() {
            return format!(
                "v2|{}|{}",
                token.owner_id,
                token
                    .holders
                    .iter()
                    .map(|h| format!("{}:{}:{}", h.id, h.kind.to_code(), h.time_ns))
                    .collect::<Vec<_>>()
                    .join(",")
            );
        }
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
        if let Some(body) = raw.strip_prefix("v2|") {
            let invalid = || PathLockError::InvalidToken(raw.into());
            let (owner, refs) = body.split_once('|').ok_or_else(invalid)?;
            if !Self::valid_id(owner) || refs.is_empty() {
                return Err(invalid());
            }
            let mut token = LockToken {
                owner_id: owner.into(),
                time_ns: 0,
                lock_type: PathLockKind::Exact,
                holders: Vec::new(),
            };
            for item in refs.split(',') {
                let parts: Vec<_> = item.split(':').collect();
                if parts.len() != 3
                    || !Self::valid_id(parts[0])
                    || parts[1].len() != 1
                    || parts[2].is_empty()
                    || !parts[2].bytes().all(|b| b.is_ascii_digit())
                    || token.holders.iter().any(|h| h.id == parts[0])
                {
                    return Err(invalid());
                }
                token.holders.push(LockHolder {
                    id: parts[0].into(),
                    kind: PathLockKind::from_code(parts[1].chars().next().unwrap())
                        .ok_or_else(invalid)?,
                    time_ns: parts[2].parse().map_err(|_| invalid())?,
                });
            }
            token.recompute();
            return Ok(token);
        }
        let (owner_id, rest) = raw
            .split_once(':')
            .ok_or_else(|| PathLockError::InvalidToken(format!("missing ':' in token: {raw}")))?;

        let (time_ns_str, rest) = rest.split_once(':').ok_or_else(|| {
            PathLockError::InvalidToken(format!("missing second ':' in token: {raw}"))
        })?;
        if !Self::valid_id(owner_id)
            || time_ns_str.is_empty()
            || !time_ns_str.bytes().all(|b| b.is_ascii_digit())
        {
            return Err(PathLockError::InvalidToken(raw.into()));
        }

        let time_ns: u128 = time_ns_str
            .parse()
            .map_err(|_| PathLockError::InvalidToken(format!("invalid time_ns in token: {raw}")))?;

        let mut lock_type_chars = rest.chars();
        let lock_type_char = lock_type_chars.next().ok_or_else(|| {
            PathLockError::InvalidToken(format!("empty lock type in token: {raw}"))
        })?;
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
            holders: Vec::new(),
        })
    }

    /// Validate identities before placing them in the delimited wire format.
    pub fn valid_id(id: &str) -> bool {
        !id.is_empty() && !id.chars().any(|c| c.is_control() || ":|,".contains(c))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_supported_lock_types() {
        for token in [
            LockToken {
                holders: Vec::new(),
                owner_id: "abc-123".to_string(),
                time_ns: 1700000000000000000,
                lock_type: PathLockKind::Exact,
            },
            LockToken {
                holders: Vec::new(),
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
            ":100:E",
            "a:+1:E",
            "v2|owner|h:E:1,h:T:2",
            "v2|owner|h:E:1,",
            "v2|owner|h:P:1",
            "v2|owner|h:E:340282366920938463463374607431768211456",
        ] {
            assert!(matches!(
                LockTokenCodec::decode(raw),
                Err(PathLockError::InvalidToken(_))
            ));
        }
    }
}
