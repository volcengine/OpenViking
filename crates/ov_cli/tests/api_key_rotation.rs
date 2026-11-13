//! Exercise the real command and an isolated selected config, never the user's config.
use serde_json::{Value, json};
use std::{
    fs,
    io::{Read, Write},
    net::TcpListener,
    path::Path,
    process::{Command, Output, Stdio},
    thread,
    time::{Duration, Instant},
};

const OLD_KEY: &str = "YWNtZQ.YWxpY2U.b2xkLXNlY3JldA";
const NEW_KEY: &str = "YWNtZQ.YWxpY2U.bmV3LXNlY3JldA";

fn run(dir: &Path, args: &[&str]) -> Output {
    let home = dir.join("home");
    let settings_dir = home.join(".openviking");
    fs::create_dir_all(&settings_dir).unwrap();
    fs::write(
        settings_dir.join("ovcli.settings.conf"),
        json!({"language": "en"}).to_string(),
    )
    .unwrap();
    Command::new(env!("CARGO_BIN_EXE_ov"))
        .current_dir(dir)
        .env("HOME", &home)
        .env("USERPROFILE", &home)
        .env("OPENVIKING_CLI_CONFIG_FILE", "selected.json")
        .env("OPENVIKING_LANG", "en")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .args(args)
        .output()
        .unwrap()
}

fn serve(
    status: u16,
    body: Value,
    before_response: impl FnOnce() + Send + 'static,
) -> (String, thread::JoinHandle<String>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    listener.set_nonblocking(true).unwrap();
    let server = thread::spawn(move || {
        let started = Instant::now();
        let mut stream = loop {
            match listener.accept() {
                Ok((stream, _)) => break stream,
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                    assert!(
                        started.elapsed() < Duration::from_secs(10),
                        "CLI sent no request"
                    );
                    thread::sleep(Duration::from_millis(10));
                }
                Err(error) => panic!("{error}"),
            }
        };
        stream.set_nonblocking(false).unwrap();
        stream
            .set_read_timeout(Some(Duration::from_secs(5)))
            .unwrap();
        let mut request = Vec::new();
        let mut buf = [0; 4096];
        loop {
            let n = stream.read(&mut buf).unwrap();
            assert!(n > 0);
            request.extend_from_slice(&buf[..n]);
            if let Some(header_end) = request.windows(4).position(|bytes| bytes == b"\r\n\r\n") {
                let headers = String::from_utf8_lossy(&request[..header_end]);
                let content_length: usize = headers
                    .lines()
                    .filter_map(|line| line.split_once(':'))
                    .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                    .map(|(_, value)| value.trim().parse().unwrap())
                    .unwrap_or(0);
                if request.len() >= header_end + 4 + content_length {
                    break;
                }
            }
        }
        before_response();
        let body = body.to_string();
        write!(stream, "HTTP/1.1 {status} Test\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
        String::from_utf8(request).unwrap()
    });
    (url, server)
}

fn seed(dir: &Path, url: &str) -> Value {
    let config = json!({
        "url": url, "api_key": OLD_KEY, "root_api_key": "root-secret",
        "echo_command": false, "timeout": 5,
        "plugin": {"capture": {"enabled": true}}, "unknown": [1, 2],
        // Identity headers are not authoritative in API-key mode.
        "account": "stale-account", "user": "stale-user"
    });
    fs::write(dir.join("selected.json"), config.to_string()).unwrap();
    fs::write(dir.join("ovcli.conf"), "unrelated config").unwrap();
    config
}

#[test]
fn self_rotation_updates_only_selected_api_key_and_announces_it() {
    let (url, server) = serve(
        200,
        json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
        || {},
    );
    let dir = tempfile::tempdir().unwrap();
    let mut expected = seed(dir.path(), &url);
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    let request = server.join().unwrap();
    assert!(request.contains("POST /api/v1/admin/accounts/acme/users/alice/key"));
    assert!(request.contains(OLD_KEY));
    assert!(result.status.success(), "{result:?}");
    assert_eq!(
        serde_json::from_slice::<Value>(&result.stdout).unwrap()["result"]["user_key"],
        NEW_KEY
    );
    let notice = String::from_utf8_lossy(&result.stderr);
    assert!(
        notice.contains("will update api_key in selected.json"),
        "{notice}"
    );
    assert!(
        notice.contains("Updated api_key in selected.json"),
        "{notice}"
    );
    assert!(!notice.contains(NEW_KEY));
    expected["api_key"] = json!(NEW_KEY);
    let saved: Value =
        serde_json::from_slice(&fs::read(dir.path().join("selected.json")).unwrap()).unwrap();
    assert_eq!(saved, expected);
    assert_eq!(
        fs::read_to_string(dir.path().join("ovcli.conf")).unwrap(),
        "unrelated config"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(dir.path().join("selected.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }
}

#[test]
fn failed_rotation_keeps_config_unchanged() {
    let (url, server) = serve(
        403,
        json!({"status":"error", "error":{"code":"PERMISSION_DENIED","message":"denied"}}),
        || {},
    );
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), &url);
    let original = fs::read(dir.path().join("selected.json")).unwrap();
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    server.join().unwrap();
    assert!(!result.status.success());
    assert_eq!(
        fs::read(dir.path().join("selected.json")).unwrap(),
        original
    );
    assert!(!String::from_utf8_lossy(&result.stderr).contains("Updated api_key"));
    assert!(!fs::read_dir(dir.path()).unwrap().any(|entry| {
        entry
            .unwrap()
            .file_name()
            .to_string_lossy()
            .ends_with(".tmp")
    }));
}

#[test]
fn concurrent_config_edit_is_preserved_and_rotated_key_is_recoverable() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("selected.json");
    let (url, server) = serve(
        200,
        json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
        move || {
            let mut config: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
            config["plugin"] = json!({"changed": true});
            fs::write(path, config.to_string()).unwrap();
        },
    );
    let mut expected = seed(dir.path(), &url);
    expected["plugin"] = json!({"changed": true});
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    server.join().unwrap();
    assert!(!result.status.success());
    assert_eq!(
        serde_json::from_slice::<Value>(&result.stdout).unwrap()["result"]["user_key"],
        NEW_KEY
    );
    let notice = String::from_utf8_lossy(&result.stderr);
    assert!(
        notice.contains("API key was rotated, but could not update"),
        "{notice}"
    );
    assert!(notice.contains("manually"), "{notice}");
    assert_eq!(
        serde_json::from_slice::<Value>(&fs::read(dir.path().join("selected.json")).unwrap())
            .unwrap(),
        expected
    );
}

#[test]
fn rotating_another_user_or_using_sudo_does_not_replace_personal_config() {
    for args in [
        vec!["admin", "regenerate-key", "acme", "bob", "-o", "json"],
        vec![
            "--sudo",
            "admin",
            "regenerate-key",
            "acme",
            "alice",
            "-o",
            "json",
        ],
    ] {
        let (url, server) = serve(
            200,
            json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
            || {},
        );
        let dir = tempfile::tempdir().unwrap();
        seed(dir.path(), &url);
        let original = fs::read(dir.path().join("selected.json")).unwrap();
        let result = run(dir.path(), &args);
        server.join().unwrap();
        assert!(result.status.success(), "{result:?}");
        assert_eq!(
            fs::read(dir.path().join("selected.json")).unwrap(),
            original
        );
        assert!(!String::from_utf8_lossy(&result.stderr).contains("update api_key"));
    }
}

#[test]
fn rotation_updates_the_effective_credential_and_matching_custom_headers() {
    for (field, api_key, headers) in [
        (
            "extra_headers",
            json!(OLD_KEY),
            json!({"x-aPi-kEy": OLD_KEY, "Authorization": format!("Bearer {OLD_KEY}")}),
        ),
        (
            "extra_header",
            json!(OLD_KEY),
            json!({"X-API-Key": OLD_KEY}),
        ),
        (
            "extra_headers",
            json!("unused-key"),
            json!({"X-API-Key": OLD_KEY}),
        ),
        (
            "extra_headers",
            Value::Null,
            json!({"authorization": format!("Bearer {OLD_KEY}")}),
        ),
    ] {
        let dir = tempfile::tempdir().unwrap();
        let (url, server) = serve(
            200,
            json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
            || {},
        );
        let mut config = seed(dir.path(), &url);
        config["api_key"] = api_key;
        if config["api_key"].is_null() {
            // The Bearer-only row has no personal api_key: without dropping the
            // root fallback, admin commands would send X-API-Key: root-secret
            // and the Bearer header would never be the effective credential.
            config.as_object_mut().unwrap().remove("root_api_key");
        }
        config[field] = headers;
        config[field]["X-Custom"] = json!("preserved");
        let path = dir.path().join("selected.json");
        fs::write(&path, config.to_string()).unwrap();
        let result = run(
            dir.path(),
            &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
        );
        server.join().unwrap();
        assert!(result.status.success(), "{result:?}");
        let mut saved: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        assert_eq!(saved["api_key"], NEW_KEY);
        assert_eq!(saved[field]["X-Custom"], "preserved");
        assert!(!saved.to_string().contains(OLD_KEY));
        assert!(
            String::from_utf8_lossy(&result.stderr).contains("matching authentication headers")
        );

        // A fresh CLI invocation must authenticate with the rotated key.
        let (url, server) = serve(200, json!({"status":"ok", "result":[]}), || {});
        saved["url"] = json!(url);
        fs::write(&path, saved.to_string()).unwrap();
        let result = run(dir.path(), &["admin", "list-users", "acme", "-o", "json"]);
        let request = server.join().unwrap();
        assert!(result.status.success(), "{result:?}");
        assert!(request.contains(NEW_KEY));
        assert!(!request.contains(OLD_KEY));
    }
}

#[test]
fn conflicting_header_credentials_fail_before_rotation() {
    let dir = tempfile::tempdir().unwrap();
    let mut config = seed(dir.path(), "http://127.0.0.1:1");
    config["extra_headers"] = json!({"X-API-Key": OLD_KEY, "x-api-key": NEW_KEY});
    let path = dir.path().join("selected.json");
    fs::write(&path, config.to_string()).unwrap();
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("Conflicting X-API-Key headers"));
    assert_eq!(fs::read_to_string(path).unwrap(), config.to_string());
}

#[cfg(unix)]
#[test]
fn rotation_preserves_config_symlinks_and_updates_the_target() {
    let dir = tempfile::tempdir().unwrap();
    let (url, server) = serve(
        200,
        json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
        || {},
    );
    let mut expected = seed(dir.path(), &url);
    let selected = dir.path().join("selected.json");
    fs::create_dir(dir.path().join("shared")).unwrap();
    let target = dir.path().join("shared/ovcli.conf");
    fs::rename(&selected, &target).unwrap();
    std::os::unix::fs::symlink("shared/ovcli.conf", &selected).unwrap();
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    server.join().unwrap();
    assert!(result.status.success(), "{result:?}");
    assert_eq!(
        fs::read_link(&selected).unwrap(),
        Path::new("shared/ovcli.conf")
    );
    expected["api_key"] = json!(NEW_KEY);
    assert_eq!(
        serde_json::from_slice::<Value>(&fs::read(target).unwrap()).unwrap(),
        expected
    );
    assert_eq!(
        fs::read(selected).unwrap(),
        serde_json::to_vec_pretty(&expected).unwrap()
    );
}

#[cfg(unix)]
#[test]
fn retargeting_config_symlink_during_rotation_preserves_both_files() {
    let dir = tempfile::tempdir().unwrap();
    let selected = dir.path().join("selected.json");
    let changed_link = selected.clone();
    let (url, server) = serve(
        200,
        json!({"status":"ok", "result":{"user_key":NEW_KEY}}),
        move || {
            fs::remove_file(&changed_link).unwrap();
            std::os::unix::fs::symlink("other.json", changed_link).unwrap();
        },
    );
    let config = seed(dir.path(), &url);
    let target = dir.path().join("target.json");
    let other = dir.path().join("other.json");
    fs::rename(&selected, &target).unwrap();
    fs::copy(&target, &other).unwrap();
    std::os::unix::fs::symlink("target.json", &selected).unwrap();
    let result = run(
        dir.path(),
        &["admin", "regenerate-key", "acme", "alice", "-o", "json"],
    );
    server.join().unwrap();
    assert!(!result.status.success());
    assert_eq!(fs::read_link(&selected).unwrap(), Path::new("other.json"));
    for file in [target, other] {
        assert_eq!(fs::read_to_string(file).unwrap(), config.to_string());
    }
    assert_eq!(
        serde_json::from_slice::<Value>(&result.stdout).unwrap()["result"]["user_key"],
        NEW_KEY
    );
    assert!(String::from_utf8_lossy(&result.stderr).contains("Config changed during key rotation"));
}
