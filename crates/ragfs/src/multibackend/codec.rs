//! Stable binary codecs for V2 segment and checkpoint files.

use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::core::errors::{Error, Result};
use crate::multibackend::constants::{MAX_CHECKPOINT_NODES, MAX_SEGMENT_RECORDS};
use crate::multibackend::model::{CheckpointNode, DecodedSegment, SegmentEventType, SegmentRecord};

const SEGMENT_MAGIC: &[u8; 4] = b"OVSG";
const CHECKPOINT_MAGIC: &[u8; 4] = b"OVCP";
const FORMAT_VERSION: u16 = 1;
const MAX_STRING_LEN: usize = 1024 * 1024;
const MIN_RECORD_LEN: usize = 14;
const MAX_RECORD_LEN: usize = 2 * 1024 * 1024;
const MAX_CHECKPOINT_DEPTH: usize = 256;

/// Encode segment records in the stable OVSG binary format.
pub fn encode_segment(records: &[SegmentRecord]) -> Result<Vec<u8>> {
    if records.len() > MAX_SEGMENT_RECORDS {
        return Err(serialization("segment has too many records"));
    }

    let mut bytes = Vec::new();
    bytes.extend_from_slice(SEGMENT_MAGIC);
    bytes.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    for record in records {
        record.validate()?;
        let frame = encode_record(record)?;
        if frame.len() > MAX_RECORD_LEN {
            return Err(serialization("segment record is too large"));
        }
        bytes.extend_from_slice(&(frame.len() as u32).to_le_bytes());
        bytes.extend_from_slice(&frame);
    }
    Ok(bytes)
}

/// Decode complete segment records and recover a truncated final frame.
pub fn decode_segment(bytes: &[u8]) -> Result<DecodedSegment> {
    let mut reader = Reader::new(bytes);
    read_header(&mut reader, SEGMENT_MAGIC)?;
    let mut records = Vec::new();
    let mut valid_bytes = reader.position();
    let mut truncated_tail = false;

    while reader.remaining() > 0 {
        if records.len() == MAX_SEGMENT_RECORDS {
            return Err(serialization("segment has too many records"));
        }
        let frame_start = reader.position();
        if reader.remaining() < 4 {
            truncated_tail = true;
            break;
        }
        let frame_len = reader.read_u32()? as usize;
        if frame_len < MIN_RECORD_LEN {
            return Err(serialization("segment record is too small"));
        }
        if frame_len > MAX_RECORD_LEN {
            return Err(serialization("segment record is too large"));
        }
        if frame_len > reader.remaining() {
            truncated_tail = true;
            break;
        }
        let mut frame = Reader::new(reader.read_exact(frame_len)?);
        let record = decode_record(&mut frame)?;
        if frame.remaining() != 0 {
            return Err(serialization("segment record has trailing bytes"));
        }
        records.push(record);
        valid_bytes = reader.position();
        debug_assert!(valid_bytes > frame_start);
    }

    Ok(DecodedSegment {
        records,
        valid_bytes: valid_bytes as u64,
        truncated_tail,
    })
}

/// Encode one checkpoint tree in the stable OVCP binary format.
pub fn encode_checkpoint_chunk(root: &CheckpointNode) -> Result<Vec<u8>> {
    let node_count = validate_checkpoint_shape(root)?;
    root.validate()?;

    let mut bytes = Vec::new();
    bytes.extend_from_slice(CHECKPOINT_MAGIC);
    bytes.extend_from_slice(&FORMAT_VERSION.to_le_bytes());
    bytes.extend_from_slice(&(node_count as u32).to_le_bytes());
    encode_checkpoint_node(root, &mut bytes)?;
    Ok(bytes)
}

/// Decode one complete OVCP checkpoint tree.
pub fn decode_checkpoint_chunk(bytes: &[u8]) -> Result<CheckpointNode> {
    let mut reader = Reader::new(bytes);
    read_header(&mut reader, CHECKPOINT_MAGIC)?;
    let node_count = reader.read_u32()? as usize;
    if node_count == 0 || node_count > MAX_CHECKPOINT_NODES {
        return Err(serialization("invalid checkpoint node count"));
    }

    let mut remaining_nodes = node_count;
    let root = decode_checkpoint_node(&mut reader, &mut remaining_nodes, 1)?;
    if remaining_nodes != 0 {
        return Err(serialization(
            "checkpoint node count does not match payload",
        ));
    }
    if reader.remaining() != 0 {
        return Err(serialization("checkpoint has trailing bytes"));
    }
    root.validate()?;
    Ok(root)
}

/// Return a lowercase hexadecimal SHA-256 digest.
pub fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

/// Encode one validated segment record payload.
fn encode_record(record: &SegmentRecord) -> Result<Vec<u8>> {
    let mut bytes = Vec::new();
    bytes.extend_from_slice(&record.seq.to_le_bytes());
    bytes.push(record.event_type as u8);
    let flags =
        u8::from(record.op_id.is_some()) | (u8::from(record.destination_path.is_some()) << 1);
    bytes.push(flags);
    write_string(&mut bytes, &record.path)?;
    if let Some(op_id) = record.op_id {
        bytes.extend_from_slice(op_id.as_bytes());
    }
    if let Some(destination) = &record.destination_path {
        write_string(&mut bytes, destination)?;
    }
    Ok(bytes)
}

/// Decode and validate one complete segment record payload.
fn decode_record(reader: &mut Reader<'_>) -> Result<SegmentRecord> {
    let seq = reader.read_u64()?;
    let event_type = match reader.read_u8()? {
        1 => SegmentEventType::Write,
        2 => SegmentEventType::Remove,
        3 => SegmentEventType::RemoveTree,
        4 => SegmentEventType::MoveTree,
        _ => return Err(serialization("unknown segment event type")),
    };
    let flags = reader.read_u8()?;
    if flags & !0b11 != 0 {
        return Err(serialization("unknown segment record flags"));
    }
    let path = reader.read_string()?;
    let op_id = if flags & 1 != 0 {
        let raw: [u8; 16] = reader
            .read_exact(16)?
            .try_into()
            .map_err(|_| serialization("invalid operation id"))?;
        Some(Uuid::from_bytes(raw))
    } else {
        None
    };
    let destination_path = if flags & 2 != 0 {
        Some(reader.read_string()?)
    } else {
        None
    };
    let record = SegmentRecord {
        seq,
        event_type,
        path,
        op_id,
        destination_path,
    };
    record.validate()?;
    Ok(record)
}

/// Validate checkpoint size and depth without recursive traversal.
fn validate_checkpoint_shape(root: &CheckpointNode) -> Result<usize> {
    let mut stack = vec![(root, 1_usize)];
    let mut node_count = 0_usize;
    while let Some((node, depth)) = stack.pop() {
        if depth > MAX_CHECKPOINT_DEPTH {
            return Err(serialization("checkpoint exceeds maximum depth"));
        }
        node_count += 1;
        if node_count > MAX_CHECKPOINT_NODES {
            return Err(serialization("checkpoint has too many nodes"));
        }
        if node.path_fragment.len() > MAX_STRING_LEN {
            return Err(serialization("checkpoint path fragment is too large"));
        }
        stack.extend(node.children.iter().map(|child| (child, depth + 1)));
    }
    Ok(node_count)
}

/// Encode one checkpoint node after iterative shape validation.
fn encode_checkpoint_node(node: &CheckpointNode, bytes: &mut Vec<u8>) -> Result<()> {
    write_string(bytes, &node.path_fragment)?;
    let flags = u8::from(node.latest_seq.is_some()) | (u8::from(node.deleted.is_some()) << 1);
    bytes.push(flags);
    if let Some(seq) = node.latest_seq {
        bytes.extend_from_slice(&seq.to_le_bytes());
    }
    bytes.extend_from_slice(&(node.children.len() as u32).to_le_bytes());
    for child in &node.children {
        encode_checkpoint_node(child, bytes)?;
    }
    Ok(())
}

/// Decode one node while enforcing global node and depth budgets before allocation.
fn decode_checkpoint_node(
    reader: &mut Reader<'_>,
    remaining_nodes: &mut usize,
    depth: usize,
) -> Result<CheckpointNode> {
    if depth > MAX_CHECKPOINT_DEPTH {
        return Err(serialization("checkpoint exceeds maximum depth"));
    }
    if *remaining_nodes == 0 {
        return Err(serialization("checkpoint contains too many nodes"));
    }
    *remaining_nodes -= 1;

    let path_fragment = reader.read_string()?;
    let flags = reader.read_u8()?;
    if flags & !0b11 != 0 {
        return Err(serialization("unknown checkpoint node flags"));
    }
    let latest_seq = if flags & 1 != 0 {
        Some(reader.read_u64()?)
    } else {
        None
    };
    let deleted = if flags & 2 != 0 { Some(true) } else { None };
    let child_count = reader.read_u32()? as usize;
    if child_count > *remaining_nodes {
        return Err(serialization(
            "checkpoint child count exceeds remaining node budget",
        ));
    }

    let mut children = Vec::new();
    for _ in 0..child_count {
        children.push(decode_checkpoint_node(reader, remaining_nodes, depth + 1)?);
    }
    Ok(CheckpointNode {
        path_fragment,
        latest_seq,
        deleted,
        children,
    })
}

/// Append one bounded length-prefixed UTF-8 string.
fn write_string(bytes: &mut Vec<u8>, value: &str) -> Result<()> {
    if value.len() > MAX_STRING_LEN {
        return Err(serialization("string is too large"));
    }
    bytes.extend_from_slice(&(value.len() as u32).to_le_bytes());
    bytes.extend_from_slice(value.as_bytes());
    Ok(())
}

/// Read and validate a format magic and version.
fn read_header(reader: &mut Reader<'_>, magic: &[u8; 4]) -> Result<()> {
    if reader.read_exact(4)? != magic {
        return Err(serialization("invalid binary format magic"));
    }
    if reader.read_u16()? != FORMAT_VERSION {
        return Err(serialization("unsupported binary format version"));
    }
    Ok(())
}

/// Build a serialization error with a concise message.
fn serialization(message: impl Into<String>) -> Error {
    Error::Serialization(message.into())
}

struct Reader<'a> {
    bytes: &'a [u8],
    position: usize,
}

impl<'a> Reader<'a> {
    /// Create a reader positioned at the start of a byte slice.
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, position: 0 }
    }

    /// Return the current byte offset.
    fn position(&self) -> usize {
        self.position
    }

    /// Return the number of unread bytes.
    fn remaining(&self) -> usize {
        self.bytes.len() - self.position
    }

    /// Read an exact byte range or reject truncated input.
    fn read_exact(&mut self, length: usize) -> Result<&'a [u8]> {
        let end = self
            .position
            .checked_add(length)
            .ok_or_else(|| serialization("binary length overflow"))?;
        let value = self
            .bytes
            .get(self.position..end)
            .ok_or_else(|| serialization("truncated binary data"))?;
        self.position = end;
        Ok(value)
    }

    /// Read one unsigned byte.
    fn read_u8(&mut self) -> Result<u8> {
        Ok(self.read_exact(1)?[0])
    }

    /// Read one little-endian u16.
    fn read_u16(&mut self) -> Result<u16> {
        Ok(u16::from_le_bytes(self.read_exact(2)?.try_into().unwrap()))
    }

    /// Read one little-endian u32.
    fn read_u32(&mut self) -> Result<u32> {
        Ok(u32::from_le_bytes(self.read_exact(4)?.try_into().unwrap()))
    }

    /// Read one little-endian u64.
    fn read_u64(&mut self) -> Result<u64> {
        Ok(u64::from_le_bytes(self.read_exact(8)?.try_into().unwrap()))
    }

    /// Read one bounded length-prefixed UTF-8 string.
    fn read_string(&mut self) -> Result<String> {
        let length = self.read_u32()? as usize;
        if length > MAX_STRING_LEN {
            return Err(serialization("string is too large"));
        }
        let raw = self.read_exact(length)?;
        let value =
            std::str::from_utf8(raw).map_err(|_| serialization("string is not valid UTF-8"))?;
        Ok(value.to_owned())
    }
}

#[cfg(test)]
#[path = "tests/test_codec.rs"]
mod tests;
