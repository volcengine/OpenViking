//! Versioned enqueue envelope. The legacy `enqueue` path never inspects payloads.
use super::backend::Message;
use crate::core::errors::{Error, Result};
use uuid::Uuid;

const FIXED_HEADER_LEN: usize = 28;
const REQUIRED: u16 = 1;
const GROUP_KEY: u16 = 2;
const SERIAL_KEY: u16 = 3;
const MAX_HEADER_LEN: usize = 65_536;

pub(super) fn decode(data: &[u8]) -> Result<Message> {
    let invalid = || Error::InvalidOperation("invalid enqueue_ext v1 envelope".to_string());
    if data.len() < FIXED_HEADER_LEN
        || &data[..4] != b"QFSE"
        || u16::from_be_bytes([data[4], data[5]]) != 1
        || u16::from_be_bytes([data[6], data[7]]) != 0
    {
        return Err(invalid());
    }
    let header_len = u32::from_be_bytes(data[8..12].try_into().unwrap()) as usize;
    if !(FIXED_HEADER_LEN..=data.len().min(MAX_HEADER_LEN)).contains(&header_len) {
        return Err(invalid());
    }
    let id = Uuid::from_slice(&data[12..28]).map_err(|_| invalid())?;
    if id.is_nil() {
        return Err(invalid());
    }
    let mut group_key = None;
    let mut serial_key = None;
    let mut offset = FIXED_HEADER_LEN;
    while offset < header_len {
        if header_len - offset < 8 {
            return Err(invalid());
        }
        let kind = u16::from_be_bytes(data[offset..offset + 2].try_into().unwrap());
        let flags = u16::from_be_bytes(data[offset + 2..offset + 4].try_into().unwrap());
        let len = u32::from_be_bytes(data[offset + 4..offset + 8].try_into().unwrap()) as usize;
        offset += 8;
        if flags & !REQUIRED != 0 || len > header_len - offset {
            return Err(invalid());
        }
        let value = &data[offset..offset + len];
        if kind == GROUP_KEY || kind == SERIAL_KEY {
            let key = if kind == GROUP_KEY {
                &mut group_key
            } else {
                &mut serial_key
            };
            if key.is_some() || flags != REQUIRED {
                return Err(invalid());
            }
            *key = Some(
                std::str::from_utf8(value)
                    .map_err(|_| invalid())?
                    .to_string(),
            );
        } else if flags & REQUIRED != 0 {
            return Err(Error::NotSupported(format!(
                "unsupported required enqueue_ext TLV: {kind}"
            )));
        }
        offset += len;
    }
    let mut message = Message::new(data[header_len..].to_vec());
    message.id = id.to_string();
    message.group_key = group_key;
    message.serial_key = serial_key;
    message.validate_keys()?;
    Ok(message)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(id: Uuid, tlvs: &[(u16, u16, &[u8])], payload: &[u8]) -> Vec<u8> {
        let mut data = b"QFSE".to_vec();
        data.extend_from_slice(&1u16.to_be_bytes());
        data.extend_from_slice(&0u16.to_be_bytes());
        data.extend_from_slice(&0u32.to_be_bytes());
        data.extend_from_slice(id.as_bytes());
        for (kind, flags, value) in tlvs {
            data.extend_from_slice(&kind.to_be_bytes());
            data.extend_from_slice(&flags.to_be_bytes());
            data.extend_from_slice(&(value.len() as u32).to_be_bytes());
            data.extend_from_slice(value);
        }
        let len = data.len() as u32;
        data[8..12].copy_from_slice(&len.to_be_bytes());
        data.extend_from_slice(payload);
        data
    }

    #[test]
    fn decode_accepts_optional_keys_and_checks_all_key_constraints() {
        let id = Uuid::new_v4();
        let payload = [0, 255, b'{', b'}'];
        let plain = decode(&frame(id, &[], &payload)).unwrap();
        assert_eq!(plain.id, id.to_string());
        assert_eq!(plain.data, payload);
        assert!(plain.group_key.is_none() && plain.serial_key.is_none());
        for kind in [GROUP_KEY, SERIAL_KEY] {
            for key in ["用户:A".to_string(), "界".repeat(1365) + "a"] {
                let message = decode(&frame(
                    id,
                    &[(kind, REQUIRED, key.as_bytes()), (99, 0, b"x")],
                    &payload,
                ))
                .unwrap();
                assert_eq!(message.data, payload);
                assert_eq!(
                    message.group_key.as_deref(),
                    (kind == GROUP_KEY).then_some(key.as_str())
                );
                assert_eq!(
                    message.serial_key.as_deref(),
                    (kind == SERIAL_KEY).then_some(key.as_str())
                );
            }
            for key in [vec![], vec![b'a'; 4097], vec![255]] {
                assert!(decode(&frame(id, &[(kind, REQUIRED, &key)], &payload)).is_err());
            }
            for flags in [0, 2, 3] {
                assert!(decode(&frame(id, &[(kind, flags, b"A")], &payload)).is_err());
            }
            assert!(decode(&frame(
                id,
                &[(kind, REQUIRED, b"A"), (kind, REQUIRED, b"B")],
                &payload
            ))
            .is_err());
        }
        for tlvs in [
            vec![
                (GROUP_KEY, REQUIRED, &b"A"[..]),
                (SERIAL_KEY, REQUIRED, &b"A"[..]),
            ],
            vec![
                (SERIAL_KEY, REQUIRED, &b"A"[..]),
                (GROUP_KEY, REQUIRED, &b"A"[..]),
            ],
            vec![(99, REQUIRED, &b"A"[..])],
        ] {
            assert!(decode(&frame(id, &tlvs, &payload)).is_err());
        }
        assert!(decode(&frame(Uuid::nil(), &[], &payload)).is_err());
        let valid = frame(id, &[(SERIAL_KEY, REQUIRED, b"A")], &[]);
        for end in 0..valid.len() {
            assert!(decode(&valid[..end]).is_err());
        }
        assert_eq!(decode(&valid).unwrap().data, b"");
        let mut too_long = frame(id, &[(99, 0, &vec![0; MAX_HEADER_LEN])], &[]);
        assert!(decode(&too_long).is_err());
        too_long[8..12].copy_from_slice(&27u32.to_be_bytes());
        assert!(decode(&too_long).is_err());
    }
}
