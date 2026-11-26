//! Run each override in its own process: never mutate the test runner's environment.
use serde_json::{Value, json};
use std::{
    fs,
    process::{Command, Output, Stdio},
};

fn run(dir: &std::path::Path, selected: &str, args: &[&str]) -> Output {
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
        .env("OPENVIKING_CLI_CONFIG_FILE", selected)
        .env("OPENVIKING_LANG", "en")
        .env("NO_COLOR", "1")
        .stdin(Stdio::null())
        .args(args)
        .output()
        .unwrap()
}

fn seed(dir: &std::path::Path, name: &str, url: &str) {
    fs::write(
        dir.join(name),
        json!({"url": url, "output": "table"}).to_string(),
    )
    .unwrap();
}

#[test]
fn explicit_language_env_satisfies_startup_without_saved_settings() {
    let dir = tempfile::tempdir().unwrap();
    let home = dir.path().join("home");
    fs::create_dir(&home).unwrap();
    let settings = home.join(".openviking/ovcli.settings.conf");

    for (language, expected_code) in [
        (Some("en"), 0),
        (Some("zh-CN"), 0),
        (Some("zh_CN.UTF-8"), 0),
        (Some(""), 2),
        (Some("invalid"), 2),
        (Some("fr_FR.UTF-8"), 2),
        (None, 2),
    ] {
        let mut command = Command::new(env!("CARGO_BIN_EXE_ov"));
        command
            .current_dir(dir.path())
            .env("HOME", &home)
            .env("USERPROFILE", &home)
            .env("LANG", "en_US.UTF-8")
            .env_remove("LC_ALL")
            .env_remove("LC_MESSAGES")
            .env_remove("OPENVIKING_LANG")
            .env("NO_COLOR", "1")
            .stdin(Stdio::null())
            .arg("--version");
        if let Some(language) = language {
            command.env("OPENVIKING_LANG", language);
        }
        let result = command.output().unwrap();
        assert_eq!(
            result.status.code(),
            Some(expected_code),
            "OPENVIKING_LANG={language:?}: {result:?}"
        );
        if expected_code == 0 {
            assert!(String::from_utf8_lossy(&result.stdout).contains("openviking"));
        } else {
            assert!(
                String::from_utf8_lossy(&result.stderr)
                    .contains("needs a display language before running commands")
            );
        }
        assert!(!settings.exists(), "startup must not persist a language");
    }
}

#[test]
fn status_and_config_list_identify_the_selected_file() {
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    seed(dir.path(), "ovcli.conf.aaa", "http://127.0.0.1:9");
    seed(dir.path(), "ovcli.conf.selected", "http://127.0.0.1:9");
    let selected = dir.path().join("ovcli.conf.selected");
    let result = run(dir.path(), selected.to_str().unwrap(), &["status"]);
    let text = String::from_utf8_lossy(&result.stdout);
    assert!(result.status.success(), "{result:?}");
    assert!(text.contains("selected"), "{text}");
    assert!(text.contains("127.0.0.1:9"), "{text}");
    assert!(text.contains(dir.path().to_str().unwrap()), "{text}");
    assert!(!text.contains("default.invalid"), "{text}");
    let result = run(
        dir.path(),
        "ovcli.conf.selected",
        &["config", "list", "-o", "json"],
    );
    assert!(result.status.success(), "{result:?}");
    let data: Value = serde_json::from_slice(&result.stdout).unwrap();
    let entries = data["result"].as_array().unwrap();
    assert_eq!(entries.iter().filter(|e| e["active"] == true).count(), 1);
    assert!(
        entries
            .iter()
            .any(|e| e["name"] == "selected" && e["active"] == true)
    );
}

#[test]
fn missing_override_never_loads_another_profile() {
    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    let result = run(dir.path(), "missing.json", &["config", "show"]);
    assert!(!result.status.success());
    assert!(!String::from_utf8_lossy(&result.stdout).contains("default.invalid"));
    let result = run(dir.path(), "missing.json", &["status"]);
    assert!(!result.status.success());
}

#[test]
fn empty_override_is_rejected() {
    let dir = tempfile::tempdir().unwrap();
    let result = run(dir.path(), "", &["config", "list"]);
    assert!(!result.status.success());
    assert!(String::from_utf8_lossy(&result.stderr).contains("must not be empty"));
}

#[test]
fn connected_status_uses_the_selected_config_and_command_identity() {
    use std::collections::HashMap;
    use std::io::{BufRead, BufReader, Write};

    let dir = tempfile::tempdir().unwrap();
    seed(dir.path(), "ovcli.conf", "http://default.invalid");
    let selected = "ovcli.conf.selected";
    let cases: [(Value, &[&str], [Option<&str>; 4]); 5] = [
        (
            json!({
                "api_key": "user-key", "root_api_key": "root-key", "account": "acme",
                "user": "alice", "actor_peer_id": "peer-a", "profile": true,
                "extra_headers": {"X-Custom-Header": "custom", "Authorization": "Bearer token"},
            }),
            &["status"],
            [
                Some("user-key"),
                Some("acme"),
                Some("alice"),
                Some("peer-a"),
            ],
        ),
        (
            json!({
                "api_key": "user-key", "root_api_key": "root-key", "account": "old",
                "user": "old", "actor_peer_id": "old",
                "extra_header": {"X-Custom-Header": "custom", "Authorization": "Bearer token"},
            }),
            &[
                "system",
                "status",
                "-o",
                "json",
                "--sudo",
                "--account",
                "acme",
                "--user",
                "alice",
                "--actor-peer-id",
                "peer-b",
            ],
            [
                Some("root-key"),
                Some("acme"),
                Some("alice"),
                Some("peer-b"),
            ],
        ),
        (
            json!({"root_api_key": "root-key", "account": "acme", "user": "alice", "agent_id": "legacy"}),
            &["status"],
            [
                Some("root-key"),
                Some("acme"),
                Some("alice"),
                Some("legacy"),
            ],
        ),
        (
            json!({"account": "acme", "user": "alice"}),
            &["status"],
            [None, Some("acme"), Some("alice"), None],
        ),
        (
            json!({"api_key": "root-key", "gateway_token": "gateway-secret"}),
            &["status"],
            [Some("root-key"), None, None, None],
        ),
    ];

    for (mut config, args, identity) in cases {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        config["url"] = json!(url);
        fs::write(dir.path().join(selected), config.to_string()).unwrap();

        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream
                .set_read_timeout(Some(std::time::Duration::from_secs(5)))
                .unwrap();
            let mut request = String::new();
            let mut reader = BufReader::new(&mut stream);
            loop {
                let start = request.len();
                assert!(reader.read_line(&mut request).unwrap() > 0);
                if &request[start..] == "\r\n" {
                    break;
                }
            }
            let body = r#"{"status":"ok","result":{}}"#;
            write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            request
        });

        let result = run(dir.path(), selected, args);
        assert!(result.status.success(), "{result:?}");
        let sudo = args.contains(&"--sudo");
        if sudo {
            let output: Value = serde_json::from_slice(&result.stdout).unwrap();
            assert_eq!(output["ok"], true);
        } else {
            let text = String::from_utf8_lossy(&result.stdout);
            assert!(text.contains("selected") && text.contains(&url), "{text}");
            assert!(!text.contains("Unreachable"), "{text}");
        }
        let request = server.join().unwrap();
        let mut lines = request.lines();
        let expected_path = if sudo {
            "GET /api/v1/system/status HTTP/1.1"
        } else if config["profile"] == true {
            "GET /api/v1/observer/system?profile=1 HTTP/1.1"
        } else {
            "GET /api/v1/observer/system HTTP/1.1"
        };
        assert_eq!(lines.next(), Some(expected_path));
        let headers: HashMap<_, _> = lines
            .filter_map(|line| line.split_once(": "))
            .map(|(name, value)| (name.to_ascii_lowercase(), value))
            .collect();
        for (name, expected) in [
            "x-api-key",
            "x-openviking-account",
            "x-openviking-user",
            "x-openviking-actor-peer",
        ]
        .into_iter()
        .zip(identity)
        {
            assert_eq!(headers.get(name).copied(), expected, "{name}: {config}");
        }
        let has_extra = config
            .get("extra_headers")
            .or_else(|| config.get("extra_header"))
            .is_some();
        assert_eq!(
            headers.get("x-custom-header").copied(),
            has_extra.then_some("custom")
        );
        assert_eq!(
            headers.get("authorization").copied(),
            has_extra.then_some("Bearer token")
        );
        assert!(!headers.contains_key("x-gateway-token"));
    }
}
