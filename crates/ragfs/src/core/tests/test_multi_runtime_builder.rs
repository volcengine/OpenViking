use std::collections::HashMap;

use super::{require_runtime, CacheRuntime};

#[test]
fn selects_named_runtime_and_rejects_unknown_instance() {
    let mut runtimes = HashMap::new();
    runtimes.insert("locks".to_string(), CacheRuntime::memory());

    assert!(require_runtime(&runtimes, "locks", "PathLock").is_ok());
    assert!(require_runtime(&runtimes, "missing", "PathLock").is_err());
}
