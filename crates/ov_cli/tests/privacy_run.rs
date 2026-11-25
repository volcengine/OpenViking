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
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_ov"));
    cmd.current_dir(dir)
        .env("HOME", dir)
        .env("USERPROFILE", dir)
        .env("OPENVIKING_CLI_CONFIG_FILE", config)
        .env("OPENVIKING_LANG", "en")
        .env_remove("OPENVIKING_PRIVACY_RUN_STRICT")
        .arg("privacy")
        .kill_on_drop(true);
    cmd
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
        // Hold every connection open before replying so serial resolution times out.
        let mut streams = Vec::new();
        for _ in 0..N {
            let (stream, _) = timeout(Duration::from_secs(8), listener.accept())
                .await
                .unwrap()
                .unwrap();
            streams.push(stream);
        }
        drop(listener);
        let mut fields = HashMap::from(fields);
        for mut stream in streams.into_iter().rev() {
            let mut buf = Vec::new();
            let mut chunk = [0u8; 1024];
            let body_offset = loop {
                let n = stream.read(&mut chunk).await.unwrap();
                assert!(n > 0);
                buf.extend_from_slice(&chunk[..n]);
                if let Some(pos) = buf.windows(4).position(|w| w == b"\r\n\r\n")
                    && serde_json::from_slice::<Value>(&buf[pos + 4..]).is_ok()
                {
                    break pos + 4;
                }
            };
            assert!(buf.starts_with(b"POST /api/v1/privacy-configs/resolve "));
            let req: Value = serde_json::from_slice(&buf[body_offset..]).unwrap();
            let (status, body) = match fields.remove(req["selector"].as_str().unwrap()).unwrap() {
                Some(v) => ("200 OK", json!({"status": "ok", "result": {"value": v}})),
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
        [ "$LOGIN" = reader ] && [ "$TOKEN" = "$EXPECTED" ] &&
            [ "$1" = "header=$EXPECTED/$EXPECTED" ] || exit 9
        printf '%s' "$1"; printf '%s' "$LOGIN" >&2
    "#;
    let header = format!("header={TOKEN}/{spaced}");
    let token_env = format!("TOKEN={spaced}");
    let output = privacy(dir.path(), &url)
        .env("EXPECTED", value)
        .args([
            "run",
            "--env-file",
            "defaults.env",
            "--env-file",
            "active.env",
        ])
        .args([
            "--env", &token_env, "--", "/bin/sh", "-c", script, "sh", &header,
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

#[cfg(unix)]
#[tokio::test]
async fn strict_env_blocks_arg_references_and_allows_env_resolution() {
    let dir = tempfile::tempdir().unwrap();
    let blocked = privacy(dir.path(), "http://127.0.0.1:1")
        .env("OPENVIKING_PRIVACY_RUN_STRICT", "1")
        .args(["run", "--"])
        .args(create_marker_command())
        .arg("Bearer {{ov_privacy:private-example}}")
        .output()
        .await
        .unwrap();
    let err = String::from_utf8_lossy(&blocked.stderr);
    assert!(!blocked.status.success() && err.contains("--env or --env-file"));
    assert!(!err.contains("private-example"));
    assert!(!dir.path().join("started").exists());

    let (url, server) = resolver([(TOKEN, Some("secret-token".into()))]).await;
    let allowed = privacy(dir.path(), &url)
        .env("OPENVIKING_PRIVACY_RUN_STRICT", "1")
        .args([
            "run",
            "--env",
            &format!("TOKEN={TOKEN}"),
            "--",
            "/bin/sh",
            "-c",
            r#"printf '%s' "$TOKEN""#,
        ])
        .output()
        .await
        .unwrap();
    server.await.unwrap();
    assert!(allowed.status.success(), "{allowed:?}");
    assert_eq!(allowed.stdout, TOKEN.as_bytes());
}

#[tokio::test]
async fn invalid_invocations_keep_private_arguments_out_of_diagnostics() {
    let dir = tempfile::tempdir().unwrap();
    fs::write(dir.path().join("private.env"), "TOKEN=private-example\n").unwrap();
    for (options, arg, reason) in [
        (
            &["run", "--evn", "A=private-example"][..],
            "x",
            "Unexpected argument",
        ),
        (
            &["run", "--env-file", "private.env"],
            "private-example",
            "requires a Privacy field reference",
        ),
        (
            &["run"],
            "{{ov_privacy:private-example",
            "Incomplete Privacy reference",
        ),
    ] {
        let out = privacy(dir.path(), "http://127.0.0.1:1")
            .args(options)
            .arg("--")
            .args(create_marker_command())
            .arg(arg)
            .output()
            .await
            .unwrap();
        let err = String::from_utf8_lossy(&out.stderr);
        assert!(!out.status.success() && err.contains(reason), "{err}");
        assert!(
            !err.contains("private-example")
                && !String::from_utf8_lossy(&out.stdout).contains("private-example")
        );
        assert!(!dir.path().join("started").exists());
    }
}

#[cfg(unix)]
#[tokio::test]
async fn forwards_child_flags_and_exit_status_with_or_without_the_separator() {
    let dir = tempfile::tempdir().unwrap();
    for sep in [Some("--"), None] {
        let out = privacy(dir.path(), "http://127.0.0.1:1")
            .args(["run", "--compact", "false"])
            .args(sep)
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
        assert_eq!(out.status.code(), Some(7));
        assert_eq!(out.stdout, b"<help><--compact><false><-c><-->");
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
mod posix {
    use std::ffi::{CStr, c_char, c_int, c_ulong};
    use std::{fs::File, fs::OpenOptions, os::fd::FromRawFd, os::unix::fs::OpenOptionsExt};

    unsafe extern "C" {
        pub safe fn kill(pid: c_int, sig: c_int) -> c_int;
        pub safe fn setsid() -> c_int;
        pub fn ioctl(fd: c_int, request: c_ulong, ...) -> c_int;
        safe fn posix_openpt(flags: c_int) -> c_int;
        safe fn grantpt(fd: c_int) -> c_int;
        safe fn unlockpt(fd: c_int) -> c_int;
        fn ptsname(fd: c_int) -> *const c_char;
    }
    pub const SIGINT: c_int = 2;
    pub const SIGKILL: c_int = 9;
    pub const SIGTERM: c_int = 15;
    pub const TIOCSCTTY: c_ulong = if cfg!(target_os = "linux") {
        0x540E
    } else {
        0x2000_7461
    };
    const O_NOCTTY: c_int = if cfg!(target_os = "linux") {
        0o400
    } else {
        0x2_0000
    };

    pub fn openpty() -> (File, File) {
        let fd = posix_openpt(2 | O_NOCTTY);
        assert!(fd >= 0 && grantpt(fd) == 0 && unlockpt(fd) == 0);
        // SAFETY: fd is freshly opened; ptsname returns a NUL-terminated slave path.
        let (opened, path) = unsafe {
            let path = CStr::from_ptr(ptsname(fd)).to_str().unwrap().to_owned();
            (File::from_raw_fd(fd), path)
        };
        let slave = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(O_NOCTTY)
            .open(path)
            .unwrap();
        (opened.try_clone().unwrap(), slave)
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn detach_session(cmd: &mut Command) -> &mut Command {
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        cmd.pre_exec(|| match posix::setsid() {
            ..0 => Err(std::io::Error::last_os_error()),
            _ => Ok(()),
        })
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn detached_sh(dir: &Path, url: &str, masked: bool, script: &str) -> Command {
    let mut cmd = privacy(dir, url);
    cmd.arg("run");
    if masked {
        cmd.args(["--env", &format!("TOKEN={TOKEN}")]);
    }
    detach_session(
        cmd.args(["--", "/bin/sh", "-c", script])
            .stdin(std::process::Stdio::null()),
    );
    cmd
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
async fn wait_pid_file(path: &Path) -> i32 {
    timeout(Duration::from_secs(5), async {
        loop {
            if let Ok(text) = fs::read_to_string(path)
                && let Ok(pid) = text.trim().parse()
            {
                return pid;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .unwrap()
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
    let script = "count=0; trap 'count=$((count + 1))' INT; echo ready; while [ \"$count\" -eq 0 ]; do :; done; echo interrupted; IFS= read -r a </dev/tty || exit 7; printf '%s %s\\n' \"$a\" \"$count\"";
    let mut cmd = privacy(dir.path(), "http://127.0.0.1:1");
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::from(slave.try_clone().unwrap()))
        .args(["run", "--", "/bin/sh", "-c", script]);
    let slave_fd = slave.as_raw_fd();
    // SAFETY: setsid and ioctl are async-signal-safe; slave stays open through spawn.
    unsafe {
        cmd.pre_exec(move || {
            if posix::setsid() < 0 || posix::ioctl(slave_fd, posix::TIOCSCTTY, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = cmd.spawn().unwrap();
    drop(cmd);
    timeout(Duration::from_secs(8), async {
        let mut lines = BufReader::new(child.stdout.take().unwrap()).lines();
        assert_eq!(lines.next_line().await.unwrap().as_deref(), Some("ready"));
        master.write_all(b"\x03").unwrap();
        assert_eq!(
            lines.next_line().await.unwrap().as_deref(),
            Some("interrupted")
        );
        master.write_all(b"ok\n").unwrap();
        assert_eq!(lines.next_line().await.unwrap().as_deref(), Some("ok 1"));
        drop(slave);
        assert_eq!(child.wait().await.unwrap().code(), Some(0));
    })
    .await
    .unwrap();
    drop(master);
    let _ = drain_thread.join();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn a_masked_pipeline_flushes_non_prefix_prompts_and_stops_on_group_interrupt() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let value = "interactive-fixture";
    let (url, server) = resolver([(TOKEN, Some(value.into()))]).await;
    let script = r#"sh -c 'printf "%s\n" "$$" > up.pid; printf "ready?"; IFS= read -r l; printf "%s\n" "$l"; exec sleep 20' | cat"#;
    let mut child = detached_sh(dir.path(), &url, true, script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
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
        let up_pid = wait_pid_file(&dir.path().join("up.pid")).await;
        assert_eq!(posix::kill(-(child.id().unwrap() as i32), posix::SIGINT), 0);
        assert!(matches!(child.wait().await.unwrap().code(), Some(0 | 130)));
        stdout.read_to_end(&mut Vec::new()).await.unwrap();
        wait_until_stopped(up_pid).await;
    })
    .await
    .unwrap();
    server.await.unwrap();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
fn has_group_ov_process(pgid: i32) -> bool {
    #[cfg(target_os = "linux")]
    {
        if let Ok(entries) = fs::read_dir("/proc") {
            for entry in entries.flatten() {
                if let Ok(stat) = fs::read_to_string(entry.path().join("stat"))
                    && let Some((head, tail)) = stat.rsplit_once(") ")
                    && head.ends_with("(ov")
                    && tail.split_whitespace().nth(2) == Some(&pgid.to_string())
                {
                    return true;
                }
            }
        }
        false
    }
    #[cfg(target_os = "macos")]
    {
        let out = std::process::Command::new("ps")
            .args(["-axo", "pgid=,comm="])
            .output()
            .unwrap();
        String::from_utf8_lossy(&out.stdout).lines().any(|line| {
            let mut parts = line.split_whitespace();
            parts.next() == Some(&pgid.to_string())
                && parts
                    .next()
                    .is_some_and(|comm| comm == "ov" || comm.ends_with("/ov"))
        })
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn non_terminal_signals_and_background_lifecycle() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();

    // 1. Direct-PID SIGTERM forwards once, and group SIGINT is not duplicated into child cleanup.
    for (sig_name, group, sig) in [
        ("TERM", false, posix::SIGTERM),
        ("INT", true, posix::SIGINT),
    ] {
        let script = format!(
            "c=0; trap 'c=$((c + 1))' {sig_name}; echo ready; while [ \"$c\" -eq 0 ]; do :; done; i=0; while [ \"$i\" -lt 5000 ]; do i=$((i + 1)); done; echo \"$c\"; exit 0"
        );
        let mut child = detached_sh(dir.path(), "http://127.0.0.1:1", false, &script)
            .stdout(Stdio::piped())
            .spawn()
            .unwrap();
        timeout(Duration::from_secs(5), async {
            let mut stdout = BufReader::new(child.stdout.take().unwrap());
            let mut line = String::new();
            stdout.read_line(&mut line).await.unwrap();
            assert_eq!(line, "ready\n");
            let id = child.id().unwrap() as i32;
            assert_eq!(posix::kill(if group { -id } else { id }, sig), 0);
            line.clear();
            stdout.read_line(&mut line).await.unwrap();
            assert_eq!(line, "1\n", "{sig_name}");
            assert_eq!(child.wait().await.unwrap().code(), Some(0));
        })
        .await
        .unwrap();
    }

    // 2. Process-group SIGKILL terminates file-redirected child trees cleanly.
    let log = fs::File::create(dir.path().join("direct.log")).unwrap();
    let mut direct_child = detached_sh(
        dir.path(),
        "http://127.0.0.1:1",
        false,
        "printf '%s\\n' \"$$\" > direct.pid; /bin/sleep 30",
    )
    .stdout(Stdio::from(log.try_clone().unwrap()))
    .stderr(Stdio::from(log))
    .spawn()
    .unwrap();
    let direct_pgid = direct_child.id().unwrap() as i32;
    let direct_pid = wait_pid_file(&dir.path().join("direct.pid")).await;
    assert_eq!(posix::kill(-direct_pgid, posix::SIGKILL), 0);
    direct_child.wait().await.unwrap();
    wait_until_stopped(direct_pid).await;

    // 3. Masked background service stays alive with zero leftover ov processes until group kill.
    fs::write(
        dir.path().join("worker.sh"),
        "printf '%s\\n' \"$$\" > worker.pid\n/bin/sleep 5 && : > worker.done\n",
    )
    .unwrap();
    let (url, server) = resolver([(TOKEN, Some("bg-secret".into()))]).await;
    let mut bg = detached_sh(
        dir.path(),
        &url,
        true,
        "/bin/sh ./worker.sh >job.log 2>&1 &",
    )
    .stdout(Stdio::null())
    .stderr(Stdio::null())
    .spawn()
    .unwrap();
    let bg_pgid = bg.id().unwrap() as i32;
    assert_eq!(bg.wait().await.unwrap().code(), Some(0));
    server.await.unwrap();
    let worker_pid = wait_pid_file(&dir.path().join("worker.pid")).await;
    assert!(!process_stopped(worker_pid));
    assert!(!has_group_ov_process(bg_pgid));
    assert_eq!(posix::kill(-bg_pgid, posix::SIGKILL), 0);
    wait_until_stopped(worker_pid).await;
    assert!(!dir.path().join("worker.done").exists());
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
    let output = detached_sh(
        dir.path(),
        "http://127.0.0.1:1",
        false,
        "[ -f /dev/fd/1 ] && printf 'ok\\n' && printf 'err\\n' >&2",
    )
    .stdout(Stdio::from(fs::File::create(&out_path).unwrap()))
    .stderr(Stdio::piped())
    .spawn()
    .unwrap()
    .wait_with_output()
    .await
    .unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(fs::read_to_string(&out_path).unwrap(), "ok\n");
    assert_eq!(output.stderr, b"err\n");

    // 2. Shared 2>&1 regular-file descriptors preserve interleaved stdout/stderr order when masking.
    let shared_path = dir.path().join("shared.log");
    let shared_out = fs::File::create(&shared_path).unwrap();
    let shared_err = shared_out.try_clone().unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-shared".into()))]).await;
    let status = detached_sh(
        dir.path(),
        &url,
        true,
        r#"printf '1\n'; printf '2:%s\n' "$TOKEN" >&2; printf '3\n'; printf '4\n' >&2"#,
    )
    .stdout(Stdio::from(shared_out))
    .stderr(Stdio::from(shared_err))
    .status()
    .await
    .unwrap();
    assert_eq!(status.code(), Some(0));
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
    let status = detached_sh(dir.path(), &url, true, r#"printf 'warn:%s\n' "$TOKEN" >&2"#)
        .stdout(Stdio::from(sep_out.try_clone().unwrap()))
        .stderr(Stdio::from(sep_err))
        .status()
        .await
        .unwrap();
    assert_eq!(status.code(), Some(0));
    server.await.unwrap();
    let mut delta = String::new();
    sep_out.read_to_string(&mut delta).unwrap();
    assert_eq!(delta, format!("cursor\nwarn:{TOKEN}\n"));
    assert_eq!(
        fs::read_to_string(&sep_path).unwrap(),
        format!("before\ncursor\nwarn:{TOKEN}\n")
    );
}
