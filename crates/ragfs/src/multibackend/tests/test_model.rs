use super::*;
use std::collections::BTreeMap;
use uuid::Uuid;

/// Verifies complete routing and directory marker ownership invariants.
#[test]
fn partitions_manifest_rejects_route_gaps_and_stale_markers() {
    let manifest = PartitionsManifest {
        version: 1,
        epoch: 3,
        vbuckets: VBUCKETS,
        partitions: BTreeMap::from([(
            0,
            PartitionEntry {
                state: PartitionState::Stable,
            },
        )]),
        routes: vec![RouteEntry {
            start: 0,
            end: VBUCKETS - 1,
            partition: 0,
        }],
        directory_events: vec![DirectoryEvent {
            op_id: Uuid::from_u128(1),
            operation: DirectoryOperation::RemoveTree,
            source_path: "/local/account/removed".to_string(),
            destination_path: None,
            positions: vec![MarkerPosition {
                partition_id: 0,
                epoch: 3,
                seq: 1,
            }],
        }],
    };

    assert!(manifest.validate().is_ok());

    let mut route_gap = manifest.clone();
    route_gap.routes[0].start = 1;
    assert!(route_gap.validate().is_err());

    let mut stale_marker = manifest;
    stale_marker.directory_events[0].positions[0].epoch = 2;
    assert!(stale_marker.validate().is_err());
}
