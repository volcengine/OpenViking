//! Protocol tests for the generic write-hook registry.

use std::sync::{Arc, Mutex};

use ragfs::global_policy::register_write_hooks;
use ragfs::write_hook::{PathMatcher, WriteContext, WriteHook, WriteHookError, WriteHookRegistry};

const VALID_POLICY: &str =
    "---\ntype: global-access-policy\nschema_version: 1\ndefault: deny\nrules: []\n---\n";

struct Recorder {
    calls: Arc<Mutex<Vec<String>>>,
}

impl WriteHook for Recorder {
    fn name(&self) -> &str {
        "recorder"
    }

    fn on_write(&self, context: &WriteContext<'_>) -> Result<(), WriteHookError> {
        self.calls
            .lock()
            .expect("recorder lock")
            .push(context.path.to_string());
        Ok(())
    }
}

struct Rejector;

impl WriteHook for Rejector {
    fn name(&self) -> &str {
        "rejector"
    }

    fn on_write(&self, _context: &WriteContext<'_>) -> Result<(), WriteHookError> {
        Err(WriteHookError::new(
            self.name(),
            "TEST_REJECTED",
            "rejected by test hook",
        ))
    }
}

#[test]
fn matchers_select_exact_prefix_and_suffix_paths() {
    assert!(PathMatcher::exact("/a/b.md").matches("/a/b.md"));
    assert!(!PathMatcher::exact("/a/b.md").matches("/a/b.md.bak"));

    assert!(PathMatcher::prefix("/local/").matches("/local/acct/user/alice/GLOBAL_POLICY.md"));
    assert!(!PathMatcher::prefix("/local/").matches("/remote/x"));

    assert!(PathMatcher::suffix(".md").matches("/a/b.md"));
    assert!(!PathMatcher::suffix(".md").matches("/a/b.md.bak"));
}

#[test]
fn registry_runs_matching_hooks_in_order_and_stops_on_rejection() {
    let calls = Arc::new(Mutex::new(Vec::new()));
    let mut registry = WriteHookRegistry::new();
    registry.register(
        PathMatcher::exact("/a/b.md"),
        Arc::new(Recorder {
            calls: calls.clone(),
        }),
    );
    registry.register(PathMatcher::suffix(".md"), Arc::new(Rejector));
    registry.register(
        PathMatcher::prefix("/a/"),
        Arc::new(Recorder {
            calls: calls.clone(),
        }),
    );

    let error = registry
        .check("/a/b.md", b"data")
        .expect_err("write should be rejected");
    assert_eq!(error.code(), "TEST_REJECTED");
    assert_eq!(
        *calls.lock().expect("recorder lock"),
        vec!["/a/b.md".to_string()]
    );

    registry
        .check("/b/c.txt", b"data")
        .expect("unmatched write should pass");
}

#[test]
fn global_policy_hook_validates_only_the_reserved_path() {
    let mut registry = WriteHookRegistry::new();
    register_write_hooks(&mut registry);

    let policy_path = "/local/acct/user/alice/GLOBAL_POLICY.md";
    registry
        .check(policy_path, VALID_POLICY.as_bytes())
        .expect("valid policy should pass");

    let invalid = registry
        .check(policy_path, b"not a policy")
        .expect_err("invalid policy should be rejected");
    assert_eq!(invalid.code(), "GLOBAL_POLICY_INVALID");

    let unsupported = registry
        .check(
            policy_path,
            b"---\ntype: global-access-policy\nschema_version: 2\ndefault: deny\nrules: []\n---\n",
        )
        .expect_err("unsupported schema should be rejected");
    assert_eq!(unsupported.code(), "GLOBAL_POLICY_SCHEMA_UNSUPPORTED");

    for path in [
        "/local/acct/user/alice/resources/GLOBAL_POLICY.md",
        "/other/GLOBAL_POLICY.md",
        "/local/acct/user/alice/NOTES.md",
    ] {
        registry
            .check(path, b"not a policy")
            .expect("same-name content is ordinary");
    }
}
