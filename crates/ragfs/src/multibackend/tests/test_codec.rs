use super::*;
use uuid::Uuid;

/// Verifies segment round trips and recovery from an incomplete final frame.
#[test]
fn segment_round_trip_recovers_complete_prefix_from_truncated_tail() {
    let records = vec![
        SegmentRecord {
            seq: 1,
            event_type: SegmentEventType::Write,
            path: "/local/account/file.txt".to_string(),
            op_id: None,
            destination_path: None,
        },
        SegmentRecord {
            seq: 2,
            event_type: SegmentEventType::MoveTree,
            path: "/local/account/source".to_string(),
            op_id: Some(Uuid::from_u128(7)),
            destination_path: Some("/local/account/destination".to_string()),
        },
    ];

    let encoded = encode_segment(&records).unwrap();
    let decoded = decode_segment(&encoded).unwrap();
    assert_eq!(decoded.records, records);
    assert_eq!(decoded.valid_bytes, encoded.len() as u64);
    assert!(!decoded.truncated_tail);

    let first_record_bytes = encode_segment(&records[..1]).unwrap();
    let truncated = decode_segment(&encoded[..encoded.len() - 1]).unwrap();
    assert_eq!(truncated.records, records[..1]);
    assert_eq!(truncated.valid_bytes, first_record_bytes.len() as u64);
    assert!(truncated.truncated_tail);
}
