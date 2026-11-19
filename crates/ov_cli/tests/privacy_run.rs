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
            "--env",
            &format!("TOKEN={spaced}"),
            "--",
            "/bin/sh",
            "-c",
            script,
            "sh",
            &format!("header={TOKEN}/{spaced}"),
        ])
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
            &["run", "--evn", "A=private-example"],
            "unused",
            "Unexpected argument",
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

/// POSIX calls for Unix job-control and descriptor tests without extra crate dependencies.
#[cfg(any(target_os = "linux", target_os = "macos"))]
mod posix {
    use std::ffi::{CStr, c_char, c_int, c_ulong};
    use std::fs::{File, OpenOptions};
    use std::os::fd::FromRawFd;
    use std::os::unix::fs::OpenOptionsExt;

    unsafe extern "C" {
        pub safe fn kill(pid: c_int, sig: c_int) -> c_int;
        pub safe fn setsid() -> c_int;
        pub safe fn dup2(oldfd: c_int, newfd: c_int) -> c_int;
        pub fn ioctl(fd: c_int, request: c_ulong, ...) -> c_int;
        safe fn posix_openpt(flags: c_int) -> c_int;
        safe fn grantpt(fd: c_int) -> c_int;
        safe fn unlockpt(fd: c_int) -> c_int;
        fn ptsname(fd: c_int) -> *const c_char;
    }
    pub const SIGINT: c_int = 2;
    pub const SIGKILL: c_int = 9;
    pub const SIGTERM: c_int = 15;
    #[cfg(target_os = "linux")]
    pub const TIOCSCTTY: c_ulong = 0x540E;
    #[cfg(target_os = "macos")]
    pub const TIOCSCTTY: c_ulong = 0x2000_7461;
    #[cfg(target_os = "linux")]
    const O_NOCTTY: c_int = 0o400;
    #[cfg(target_os = "macos")]
    const O_NOCTTY: c_int = 0x2_0000;

    pub fn openpty() -> (File, File) {
        let fd = posix_openpt(2 | O_NOCTTY);
        assert!(fd >= 0 && grantpt(fd) == 0 && unlockpt(fd) == 0);
        // SAFETY: fd is a fresh descriptor owned here; ptsname returns a NUL-terminated path.
        let (opened, path) = unsafe {
            let path = CStr::from_ptr(ptsname(fd)).to_str().unwrap().to_owned();
            (File::from_raw_fd(fd), path)
        };
        let master = opened.try_clone().unwrap();
        let slave = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(O_NOCTTY)
            .open(path)
            .unwrap();
        (master, slave)
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn detach_session(command: &mut Command) -> &mut Command {
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        command.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        })
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn process_stopped(pid: i32) -> bool {
    if posix::kill(pid, 0) < 0 {
        return true;
    }
    #[cfg(target_os = "linux")]
    if let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat"))
        && let Some((_, tail)) = stat.rsplit_once(") ")
    {
        return tail.starts_with('Z');
    }
    false
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
async fn wait_until_stopped(pid: i32) {
    for _ in 0..30 {
        if process_stopped(pid) {
            return;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    panic!("process {pid} did not stop");
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn a_terminal_interrupts_once_and_stays_readable_with_redirected_stdin() {
    use std::{io::Write, os::fd::AsRawFd, process::Stdio};

    let dir = tempfile::tempdir().unwrap();
    let (mut master, slave) = posix::openpty();
    let mut drain = master.try_clone().unwrap();
    let drain_thread = std::thread::spawn(move || {
        let mut buf = [0u8; 256];
        while std::io::Read::read(&mut drain, &mut buf).is_ok_and(|n| n > 0) {}
    });
    let mut command = privacy(dir.path(), "http://127.0.0.1:1");
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(slave.try_clone().unwrap()))
        .args([
            "run",
            "--",
            "/bin/sh",
            "-c",
            r#"
                count=0
                trap 'count=$((count + 1))' INT
                echo ready
                while [ "$count" -eq 0 ]; do :; done
                echo interrupted
                IFS= read -r answer </dev/tty || exit 7
                printf '%s %s\n' "$answer" "$count"
            "#,
        ]);
    let slave_fd = slave.as_raw_fd();
    // SAFETY: setsid and ioctl are async-signal-safe; slave stays open through spawn.
    unsafe {
        command.pre_exec(move || {
            if posix::setsid() < 0 || posix::ioctl(slave_fd, posix::TIOCSCTTY, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command.spawn().unwrap();
    drop(command);
    timeout(Duration::from_secs(8), async {
        let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
        assert_eq!(lines.next_line().await.unwrap().as_deref(), Some("ready"));
        master.write_all(b"\x03").unwrap();
        assert_eq!(
            lines.next_line().await.unwrap().as_deref(),
            Some("interrupted")
        );
        master.write_all(b"answer\n").unwrap();
        assert_eq!(
            lines.next_line().await.unwrap().as_deref(),
            Some("answer 1")
        );
        drop(slave);
        assert_eq!(child.wait().await.unwrap().code(), Some(0));
    })
    .await
    .expect("terminal job did not finish");
    drop(master);
    let _ = drain_thread.join();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn a_masked_pipeline_can_prompt_for_input_and_be_interrupted_by_pid() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let up_pid_path = dir.path().join("up.pid");
    let value = "interactive-fixture";
    let (url, server) = resolver([(TOKEN, Some(value.into()))]).await;
    let mut command = privacy(dir.path(), &url);
    detach_session(
        command
            .args([
                "run",
                "--env",
                &format!("TOKEN={TOKEN}"),
                "--",
                "/bin/sh",
                "-c",
                r#"sh -c 'printf "%s\n" "$$" > up.pid; printf "ready?"; IFS= read -r line; printf "%s\n" "$line"; exec sleep 20' | cat"#,
            ])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped()),
    );
    let mut child = command.spawn().unwrap();

    timeout(Duration::from_secs(10), async {
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut prompt = [0; 6];
        stdout.read_exact(&mut prompt).await.unwrap();
        assert_eq!(&prompt, b"ready?");
        child
            .stdin
            .take()
            .unwrap()
            .write_all(format!("{value}\n").as_bytes())
            .await
            .unwrap();
        let mut echo = String::new();
        stdout.read_line(&mut echo).await.unwrap();
        assert_eq!(echo, format!("{TOKEN}\n"));
        let up_pid: i32 = fs::read_to_string(&up_pid_path)
            .unwrap()
            .trim()
            .parse()
            .unwrap();

        assert_eq!(posix::kill(child.id().unwrap() as i32, posix::SIGINT), 0);
        let code = child.wait().await.unwrap().code();
        assert!(
            matches!(code, Some(0 | 130)),
            "unexpected exit code: {code:?}"
        );
        stdout.read_to_end(&mut Vec::new()).await.unwrap();
        wait_until_stopped(up_pid).await;
    })
    .await
    .expect("prompt or pipeline cancellation did not complete");
    server.await.unwrap();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn non_terminal_signals_and_background_lifecycle() {
    use std::{os::fd::AsRawFd, process::Stdio};

    let dir = tempfile::tempdir().unwrap();
    let _ = privacy(dir.path(), "http://127.0.0.1:1");

    // 1. Group SIGTERM delivers once and preserves clean child exit status.
    let mut group_term = privacy(dir.path(), "http://127.0.0.1:1");
    detach_session(
        group_term
            .args([
                "run",
                "--",
                "/bin/sh",
                "-c",
                "count=0; trap 'count=$((count + 1))' TERM; echo ready; while [ \"$count\" -eq 0 ]; do :; done; printf '%s\\n' \"$count\"; exit 0",
            ])
            .stdout(Stdio::piped()),
    );
    let mut child = group_term.spawn().unwrap();
    timeout(Duration::from_secs(5), async {
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).await.unwrap();
        assert_eq!(line, "ready\n");
        assert_eq!(
            posix::kill(-(child.id().unwrap() as i32), posix::SIGTERM),
            0
        );
        line.clear();
        stdout.read_line(&mut line).await.unwrap();
        assert_eq!(line, "1\n");
        assert_eq!(child.wait().await.unwrap().code(), Some(0));
    })
    .await
    .unwrap();

    // 2. Single-PID SIGKILL on ov terminates file-redirected foreground children via guard.
    let direct_ready = dir.path().join("direct.ready");
    let log_file = fs::File::create(dir.path().join("direct.log")).unwrap();
    let mut direct_cmd = privacy(dir.path(), "http://127.0.0.1:1");
    detach_session(
        direct_cmd
            .args([
                "run",
                "--",
                "/bin/sh",
                "-c",
                "printf '%s\\n' \"$$\" > direct.ready; exec /bin/sleep 30",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::from(log_file.try_clone().unwrap()))
            .stderr(Stdio::from(log_file)),
    );
    let mut direct_child = direct_cmd.spawn().unwrap();
    timeout(Duration::from_secs(5), async {
        while fs::read_to_string(&direct_ready)
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let direct_pid: i32 = fs::read_to_string(&direct_ready)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        posix::kill(direct_child.id().unwrap() as i32, posix::SIGKILL),
        0
    );
    direct_child.wait().await.unwrap();
    wait_until_stopped(direct_pid).await;

    // 3. Normal return preserves a background service, releases extra caller pipes promptly,
    //    and outer SIGTERM reaches the background group cleanly for graceful shutdown.
    fs::write(
        dir.path().join("graceful.sh"),
        "trap 'printf checkpoint > graceful.saved; exit 0' TERM\nprintf '%s\\n' \"$$\" > graceful.ready\nwhile :; do /bin/sleep 1 & wait $!; done\n",
    )
    .unwrap();
    let (status_tx, mut status_rx) = tokio::net::unix::pipe::pipe().unwrap();
    let status_tx = status_tx.into_blocking_fd().unwrap();
    let status_tx_fd = status_tx.as_raw_fd();
    let mut service_outer = Command::new("/bin/sh");
    service_outer
        .current_dir(dir.path())
        .env("HOME", dir.path())
        .env("USERPROFILE", dir.path())
        .env("OPENVIKING_CLI_CONFIG_FILE", dir.path().join("ovcli.conf"))
        .env("OPENVIKING_LANG", "en")
        .args([
            "-c",
            r#""$1" privacy run -- /bin/sh -c 'printf ready >&3; exec 3>&-; /bin/sh ./graceful.sh >graceful.log 2>&1 &'; exec 3>&-; : > graceful.outer; exec /bin/sleep 10"#,
            "sh",
            env!("CARGO_BIN_EXE_ov"),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    // SAFETY: setsid and dup2 are async-signal-safe.
    unsafe {
        service_outer.pre_exec(move || {
            if posix::setsid() < 0 || posix::dup2(status_tx_fd, 3) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut service_child = service_outer.spawn().unwrap();
    drop(status_tx);
    let outer_pgid = service_child.id().unwrap() as i32;
    let graceful_ready = dir.path().join("graceful.ready");
    let graceful_outer = dir.path().join("graceful.outer");
    timeout(Duration::from_secs(5), async {
        while fs::read_to_string(&graceful_ready)
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
            || !graceful_outer.exists()
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let mut status_msg = String::new();
    timeout(
        Duration::from_millis(500),
        status_rx.read_to_string(&mut status_msg),
    )
    .await
    .expect("helper processes kept extra caller pipe open")
    .unwrap();
    assert_eq!(status_msg, "ready");

    let graceful_pid: i32 = fs::read_to_string(&graceful_ready)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert!(!process_stopped(graceful_pid));
    assert_eq!(posix::kill(-outer_pgid, posix::SIGTERM), 0);
    service_child.wait().await.unwrap();
    wait_until_stopped(graceful_pid).await;
    assert_eq!(
        fs::read_to_string(dir.path().join("graceful.saved")).unwrap(),
        "checkpoint"
    );

    // 4. Outer SIGKILL stops a background worker even when it redirected its own stdio.
    fs::write(
        dir.path().join("worker.sh"),
        "printf '%s\\n' \"$$\" > worker.ready\n/bin/sleep 2 && : > worker.done\n",
    )
    .unwrap();
    let worker_ready = dir.path().join("worker.ready");
    let next_step = dir.path().join("next_step");
    let mut kill_outer = Command::new("/bin/sh");
    detach_session(
        kill_outer
            .current_dir(dir.path())
            .env("HOME", dir.path())
            .env("USERPROFILE", dir.path())
            .env("OPENVIKING_CLI_CONFIG_FILE", dir.path().join("ovcli.conf"))
            .env("OPENVIKING_LANG", "en")
            .args([
                "-c",
                r#""$1" privacy run -- /bin/sh -c '/bin/sh ./worker.sh >job.log 2>&1 &'; : > next_step; exec /bin/sleep 10"#,
                "sh",
                env!("CARGO_BIN_EXE_ov"),
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true),
    );
    let mut kill_child = kill_outer.spawn().unwrap();
    let kill_pgid = kill_child.id().unwrap() as i32;
    timeout(Duration::from_secs(5), async {
        while fs::read_to_string(&worker_ready)
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
            || !next_step.exists()
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap();
    let worker_pid: i32 = fs::read_to_string(&worker_ready)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_eq!(posix::kill(-kill_pgid, posix::SIGKILL), 0);
    kill_child.wait().await.unwrap();
    wait_until_stopped(worker_pid).await;
    assert!(!dir.path().join("worker.done").exists());
}

#[cfg(unix)]
#[tokio::test]
async fn closing_stdout_delivers_sigpipe_without_error_output() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-line".into()))]).await;
    let mut child = privacy(dir.path(), &url)
        .args([
            "run",
            "--env",
            &format!("TOKEN={TOKEN}"),
            "--",
            "/bin/sh",
            "-c",
            r#"while :; do printf '%s\n' "$TOKEN"; done"#,
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();

    timeout(Duration::from_secs(5), async {
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut first = String::new();
        stdout.read_line(&mut first).await.unwrap();
        assert_eq!(first, format!("{TOKEN}\n"));
        drop(stdout);

        let mut stderr = Vec::new();
        child
            .stderr
            .take()
            .unwrap()
            .read_to_end(&mut stderr)
            .await
            .unwrap();
        assert!(
            stderr.is_empty(),
            "unexpected stderr: {}",
            String::from_utf8_lossy(&stderr)
        );
        assert_eq!(child.wait().await.unwrap().code(), Some(141));
    })
    .await
    .expect("closed stdout test did not finish");
    server.await.unwrap();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn preserves_regular_file_stdout_and_merged_stream_order() {
    use std::{
        io::{Read, Seek, SeekFrom},
        process::Stdio,
    };

    let dir = tempfile::tempdir().unwrap();

    // 1. Unmasked regular-file stdout stays a real file descriptor while piped stderr is relayed.
    let out_path = dir.path().join("stdout.txt");
    let mut file_cmd = privacy(dir.path(), "http://127.0.0.1:1");
    detach_session(
        file_cmd
            .args([
                "run",
                "--",
                "/bin/sh",
                "-c",
                "[ -f /dev/fd/1 ] && printf 'regular\\n' && printf 'err\\n' >&2",
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::from(fs::File::create(&out_path).unwrap()))
            .stderr(Stdio::piped()),
    );
    let output = file_cmd.spawn().unwrap().wait_with_output().await.unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(fs::read_to_string(&out_path).unwrap(), "regular\n");
    assert_eq!(output.stderr, b"err\n");

    // 2. Shared 2>&1 regular-file descriptors preserve interleaved stdout/stderr order when masking.
    let shared_path = dir.path().join("shared.log");
    let shared_out = fs::File::create(&shared_path).unwrap();
    let shared_err = shared_out.try_clone().unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-shared".into()))]).await;
    let mut shared_cmd = privacy(dir.path(), &url);
    detach_session(
        shared_cmd
            .args([
                "run",
                "--env",
                &format!("TOKEN={TOKEN}"),
                "--",
                "/bin/sh",
                "-c",
                r#"printf '1\n'; printf '2:%s\n' "$TOKEN" >&2; printf '3\n'; printf '4\n' >&2"#,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::from(shared_out))
            .stderr(Stdio::from(shared_err)),
    );
    assert_eq!(shared_cmd.status().await.unwrap().code(), Some(0));
    server.await.unwrap();
    assert_eq!(
        fs::read_to_string(&shared_path).unwrap(),
        format!("1\n2:{TOKEN}\n3\n4\n")
    );

    // 3. Independently opened descriptors to the same file keep independent offsets and cursors.
    let sep_path = dir.path().join("separate.log");
    fs::write(&sep_path, "before\ncursor\n").unwrap();
    let mut sep_out = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&sep_path)
        .unwrap();
    let sep_err = fs::OpenOptions::new().append(true).open(&sep_path).unwrap();
    sep_out
        .seek(SeekFrom::Start("before\n".len() as u64))
        .unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-sep".into()))]).await;
    let mut sep_cmd = privacy(dir.path(), &url);
    detach_session(
        sep_cmd
            .args([
                "run",
                "--env",
                &format!("TOKEN={TOKEN}"),
                "--",
                "/bin/sh",
                "-c",
                r#"printf 'warn:%s\n' "$TOKEN" >&2"#,
            ])
            .stdin(Stdio::null())
            .stdout(Stdio::from(sep_out.try_clone().unwrap()))
            .stderr(Stdio::from(sep_err)),
    );
    assert_eq!(sep_cmd.status().await.unwrap().code(), Some(0));
    server.await.unwrap();
    let mut delta = String::new();
    sep_out.read_to_string(&mut delta).unwrap();
    assert_eq!(delta, format!("cursor\nwarn:{TOKEN}\n"));
    assert_eq!(
        fs::read_to_string(&sep_path).unwrap(),
        format!("before\ncursor\nwarn:{TOKEN}\n")
    );
}
