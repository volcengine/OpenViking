use serde_json::{Value, json};
use std::{collections::HashMap, fs, path::Path, time::Duration};
use tokio::{
    io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader},
    net::TcpListener,
    process::Command,
    task::JoinHandle,
    time::timeout,
};

const TOKEN: &str = "{{ov_privacy:alice:viking://~/privacy/credential/demo#/values/token}}";

fn privacy(dir: &Path, url: &str) -> Command {
    let config = dir.join("ovcli.conf");
    fs::write(
        &config,
        json!({"url": url, "api_key": "test-key", "user": "alice", "timeout": 5}).to_string(),
    )
    .unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_ov"));
    command
        .current_dir(dir)
        .env("HOME", dir)
        .env("USERPROFILE", dir)
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .arg("privacy")
        .kill_on_drop(true);
    command
}

fn create_marker_command() -> &'static [&'static str] {
    if cfg!(windows) {
        &["cmd.exe", "/D", "/C", "mkdir", "started"]
    } else {
        &["/bin/mkdir", "started"]
    }
}

async fn resolver<const N: usize>(
    fields: [(&'static str, Option<String>); N],
) -> (String, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        // Wait for every distinct request before replying so serial resolution fails.
        let mut requests = Vec::new();
        for _ in 0..N {
            let (stream, _) = timeout(Duration::from_secs(8), listener.accept())
                .await
                .expect("missing concurrent resolve request")
                .unwrap();
            requests.push(stream);
        }
        drop(listener);
        let mut fields = HashMap::from(fields);
        for mut stream in requests.into_iter().rev() {
            let mut reader = BufReader::new(&mut stream);
            let mut headers = String::new();
            while !headers.ends_with("\r\n\r\n") {
                assert!(reader.read_line(&mut headers).await.unwrap() > 0);
            }
            assert!(headers.starts_with("POST /api/v1/privacy-configs/resolve "));
            let length = headers
                .lines()
                .filter_map(|line| line.split_once(':'))
                .find(|(name, _)| name.eq_ignore_ascii_case("content-length"))
                .unwrap()
                .1;
            let mut body = vec![0; length.trim().parse().unwrap()];
            reader.read_exact(&mut body).await.unwrap();
            let request: Value = serde_json::from_slice(&body).unwrap();
            let value = fields
                .remove(request["selector"].as_str().unwrap())
                .expect("unexpected resolve");
            let (status, body) = match value {
                Some(value) => (
                    "200 OK",
                    json!({"status": "ok", "result": {"value": value}}),
                ),
                None => (
                    "404 Not Found",
                    json!({"status": "error", "error": {"code": "NOT_FOUND", "message": "missing"}}),
                ),
            };
            let body = body.to_string();
            let reply = format!(
                "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            );
            stream.write_all(reply.as_bytes()).await.unwrap();
        }
    });
    (url, server)
}

#[cfg(unix)]
#[tokio::test]
async fn resolves_environment_and_arguments_and_masks_both_streams() {
    const LOGIN: &str = "{{ov_privacy:alice:viking://~/privacy/credential/demo#/values/login}}";
    const RETIRED: &str = "{{ov_privacy:alice:viking://~/privacy/credential/demo#/values/retired}}";

    let dir = tempfile::tempdir().unwrap();
    let value = format!("quoted 'fixture' $\n{LOGIN}");
    let (url, server) =
        resolver([(TOKEN, Some(value.clone())), (LOGIN, Some("reader".into()))]).await;
    let spaced = TOKEN.replace("{{", "{{ \t").replace("}}", " \t}}");
    fs::write(
        dir.path().join("defaults.env"),
        format!("LOGIN={RETIRED}\n"),
    )
    .unwrap();
    fs::write(
        dir.path().join("active.env"),
        format!("\u{feff}# active\r\nLOGIN='{LOGIN}'\r\nTOKEN={RETIRED}\r\n"),
    )
    .unwrap();

    let script = r#"
        [ "$LOGIN" = reader ] && [ "$TOKEN" = "$EXPECTED_TOKEN" ] &&
            [ "$1" = "header=$EXPECTED_TOKEN/$EXPECTED_TOKEN" ] || exit 9
        printf '%s' "$1"
        printf '%s' "$LOGIN" >&2
    "#;
    let output = privacy(dir.path(), &url)
        .env("EXPECTED_TOKEN", value)
        .args([
            "run",
            "--env-file",
            "defaults.env",
            "--env-file",
            "active.env",
        ])
        .args([
            "--env",
            &format!("TOKEN={spaced}"),
            "--",
            "/bin/sh",
            "-c",
            script,
        ])
        .args(["sh", &format!("header={TOKEN}/{spaced}")])
        .output()
        .await
        .unwrap();
    server.await.unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stdout, format!("header={TOKEN}/{TOKEN}").as_bytes());
    assert_eq!(output.stderr, LOGIN.as_bytes());
}

#[tokio::test]
async fn a_failed_resolve_never_starts_the_child() {
    let dir = tempfile::tempdir().unwrap();
    let (url, server) = resolver([(TOKEN, None)]).await;
    let output = privacy(dir.path(), &url)
        .args(["run", "--env", &format!("TOKEN={TOKEN}"), "--"])
        .args(create_marker_command())
        .output()
        .await
        .unwrap();
    server.await.unwrap();
    assert!(!output.status.success());
    assert!(!dir.path().join("started").exists());
    assert!(!String::from_utf8_lossy(&output.stderr).contains(TOKEN));
}

#[tokio::test]
async fn invalid_invocations_keep_private_arguments_out_of_diagnostics() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("private.env"), "TOKEN=private-example\n").unwrap();
    for (options, argument, reason) in [
        (
            &["--unknown", "run"][..],
            "private-example",
            "privacy run --help",
        ),
        (
            &["--user", "run", "run", "--compact=private-example"],
            "private-example",
            "privacy run --help",
        ),
        (
            &["run", "--env-file", "private.env"],
            "private-example",
            "requires a Privacy field reference",
        ),
        (
            &["run", "--env", "private-example=x"],
            "unused",
            "Invalid environment variable name",
        ),
        (
            &["run", "--evn", "A=private-example"],
            "unused",
            "Unexpected argument",
        ),
        (
            &["run", "--", "private-example-missing-program"],
            "unused",
            "Could not start the child command",
        ),
        (
            &["run"],
            "{{ov_privacy:private-example",
            "Incomplete Privacy reference",
        ),
    ] {
        let output = privacy(dir.path(), "http://127.0.0.1:1")
            .args(options)
            .arg("--")
            .args(create_marker_command())
            .arg(argument)
            .output()
            .await
            .unwrap();
        let error = String::from_utf8_lossy(&output.stderr);
        assert!(!output.status.success());
        assert!(error.contains(reason), "{error}");
        assert!(!error.contains("private-example"), "{error}");
        assert!(!String::from_utf8_lossy(&output.stdout).contains("private-example"));
        assert!(!dir.path().join("started").exists());
    }
}

#[cfg(unix)]
#[tokio::test]
async fn forwards_child_flags_and_exit_status_with_or_without_the_separator() {
    let dir = tempfile::tempdir().unwrap();
    for separator in [Some("--"), None] {
        let output = privacy(dir.path(), "http://127.0.0.1:1")
            .args(["run", "--compact", "false"])
            .args(separator)
            .args([
                "/bin/sh",
                "-c",
                r#"printf '<%s>' "$@"; exit 7"#,
                "sh",
                "help",
                "--compact",
                "false",
                "-c",
                "--",
            ])
            .output()
            .await
            .unwrap();
        assert_eq!(output.status.code(), Some(7), "{output:?}");
        assert_eq!(output.stdout, b"<help><--compact><false><-c><-->");
    }
}
