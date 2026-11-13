//! PathLockManager tests grouped by responsibility.

include!("common.rs");

#[path = "test_registry.rs"]
mod registry;

#[path = "test_acquire.rs"]
mod acquire;

#[path = "test_lease.rs"]
mod lease;

#[path = "test_handoff.rs"]
mod handoff;

#[path = "test_observe.rs"]
mod observe;
