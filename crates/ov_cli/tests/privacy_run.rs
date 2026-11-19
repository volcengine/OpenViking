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

/// POSIX calls for the signal tests, declared here so the crate needs no extra dependency.
#[cfg(any(target_os = "linux", target_os = "macos"))]
mod posix {
    use std::ffi::{CStr, c_char, c_int, c_ulong};
    use std::fs::{File, OpenOptions};
    use std::os::fd::FromRawFd;
    use std::os::unix::fs::OpenOptionsExt;

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
    #[cfg(target_os = "linux")]
    pub const TIOCSCTTY: c_ulong = 0x540E;
    #[cfg(target_os = "macos")]
    pub const TIOCSCTTY: c_ulong = 0x2000_7461;
    #[cfg(target_os = "linux")]
    const O_NOCTTY: c_int = 0o400;
    #[cfg(target_os = "macos")]
    const O_NOCTTY: c_int = 0x2_0000;

    /// Returns the master and slave of a new pseudo-terminal, both closed on exec.
    pub fn openpty() -> (File, File) {
        let fd = posix_openpt(2 | O_NOCTTY);
        assert!(fd >= 0 && grantpt(fd) == 0 && unlockpt(fd) == 0);
        // SAFETY: fd is a fresh descriptor owned here; ptsname returns a NUL-terminated path.
        let (opened, path) = unsafe {
            let path = CStr::from_ptr(ptsname(fd)).to_str().unwrap().to_owned();
            (File::from_raw_fd(fd), path)
        };
        // try_clone sets close-on-exec, so a failed test cannot leave the terminal open.
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
#[tokio::test]
async fn a_terminal_interrupts_once_and_stays_readable_with_redirected_stdin() {
    use std::{io::Write, os::fd::AsRawFd, process::Stdio};

    let dir = tempfile::tempdir().unwrap();
    for redirected in [false, true] {
        let (mut master, slave) = posix::openpty();
        let mut drain = master.try_clone().unwrap();
        let drain_thread = std::thread::spawn(move || {
            let mut buf = [0u8; 256];
            while std::io::Read::read(&mut drain, &mut buf).is_ok_and(|n| n > 0) {}
        });
        let stdin = if redirected {
            Stdio::null()
        } else {
            slave.try_clone().unwrap().into()
        };
        let mut command = privacy(dir.path(), "http://127.0.0.1:1");
        command.stdin(stdin).stdout(Stdio::piped()).args([
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
        // SAFETY: setsid and ioctl are async-signal-safe; the slave stays open through spawn.
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
            // The child handled Ctrl-C and exited normally, so ov reports its real status.
            assert_eq!(child.wait().await.unwrap().code(), Some(0));
        })
        .await
        .expect("the terminal job did not finish");
        drop(master);
        let _ = drain_thread.join();
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn piped_wrapper_under_controlling_terminal_forwards_pid_sigint() {
    use std::{os::fd::AsRawFd, process::Stdio};

    let dir = tempfile::tempdir().unwrap();
    let config = dir.path().join("ovcli.conf");
    fs::write(
        &config,
        json!({"url": "http://127.0.0.1:1", "api_key": "test-key", "user": "alice", "timeout": 5})
            .to_string(),
    )
    .unwrap();
    let (master, slave) = posix::openpty();
    let mut drain = master.try_clone().unwrap();
    let drain_thread = std::thread::spawn(move || {
        let mut buf = [0u8; 256];
        while std::io::Read::read(&mut drain, &mut buf).is_ok_and(|n| n > 0) {}
    });
    let ov_bin = env!("CARGO_BIN_EXE_ov");
    let mut command = Command::new("/bin/sh");
    command
        .args([
            "-c",
            r#"(sh -c 'echo $$; exec "$1" privacy run -- /bin/sh -c "(echo ready; exec sleep 30) | cat" </dev/null 2>/dev/null' sh "$1") | cat"#,
            "sh",
            ov_bin,
        ])
        .current_dir(dir.path())
        .env("HOME", dir.path())
        .env("USERPROFILE", dir.path())
        .env("OPENVIKING_CLI_CONFIG_FILE", &config)
        .env("OPENVIKING_LANG", "en")
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    let slave_fd = slave.as_raw_fd();
    // SAFETY: setsid and ioctl are async-signal-safe; the slave stays open through spawn.
    unsafe {
        command.pre_exec(move || {
            if posix::setsid() < 0 || posix::ioctl(slave_fd, posix::TIOCSCTTY, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command.spawn().unwrap();
    drop((command, slave));

    timeout(Duration::from_secs(5), async {
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut pid_line = String::new();
        stdout.read_line(&mut pid_line).await.unwrap();
        let ov_pid: i32 = pid_line.trim().parse().expect("ov pid");
        let mut ready_line = String::new();
        stdout.read_line(&mut ready_line).await.unwrap();
        assert_eq!(ready_line, "ready\n");

        assert_eq!(posix::kill(ov_pid, posix::SIGINT), 0);
        let mut rest = Vec::new();
        stdout.read_to_end(&mut rest).await.unwrap();
        child.wait().await.unwrap();
    })
    .await
    .expect("piped wrapper under controlling terminal did not exit on SIGINT");
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
    command
        .current_dir(dir.path())
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
        .stdout(Stdio::piped());
    // Detach from any terminal, as in CI, so a PID-directed SIGINT is forwarded to the group.
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        command.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
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
        let up_pid: i32 = std::fs::read_to_string(&up_pid_path)
            .unwrap()
            .trim()
            .parse()
            .unwrap();

        assert_eq!(posix::kill(child.id().unwrap() as i32, posix::SIGINT), 0);
        // With `| cat`, bash WCE may return 0 if upstream dies before `cat` receives SIGINT,
        // so also confirm both EOF on stdout and that the upstream `sleep` actually stopped.
        let code = child.wait().await.unwrap().code();
        assert!(
            matches!(code, Some(0 | 130)),
            "unexpected exit code: {code:?}"
        );
        stdout.read_to_end(&mut Vec::new()).await.unwrap();
        for _ in 0..50 {
            if posix::kill(up_pid, 0) < 0 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
        assert!(
            posix::kill(up_pid, 0) < 0,
            "upstream command {up_pid} kept running after SIGINT"
        );
    })
    .await
    .expect("the prompt or pipeline cancellation did not complete");
    server.await.unwrap();
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn killing_the_process_group_without_a_terminal_leaves_no_command_behind() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    for script in ["echo started; exec sleep 30", "sleep 30 & echo started"] {
        let mut command = privacy(dir.path(), "http://127.0.0.1:1");
        command
            .args(["run", "--", "/bin/sh", "-c", script])
            .stdout(Stdio::piped());
        // Agent harnesses start commands without a terminal and SIGKILL their group on timeout.
        // SAFETY: setsid is async-signal-safe.
        unsafe {
            command.pre_exec(|| {
                if posix::setsid() < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        let mut child = command.spawn().unwrap();
        let mut stdout = BufReader::new(child.stdout.take().unwrap());
        let mut line = String::new();
        stdout.read_line(&mut line).await.unwrap();
        assert_eq!(line, "started\n");

        // setsid made ov the leader of its own group.
        assert_eq!(
            posix::kill(-(child.id().unwrap() as i32), posix::SIGKILL),
            0
        );
        // The pipe closes only after every process holding it, sleep included, has exited.
        timeout(Duration::from_secs(5), stdout.read_to_end(&mut Vec::new()))
            .await
            .expect("the command outlived ov")
            .unwrap();
        child.wait().await.unwrap();
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn killing_outer_job_stops_redirected_background_worker() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let _ = privacy(dir.path(), "http://127.0.0.1:1");
    fs::write(
        dir.path().join("service.sh"),
        "printf '%s\\n' \"$$\" > service.pid\nexec sleep 5\n",
    )
    .unwrap();
    fs::write(
        dir.path().join("worker.sh"),
        "printf '%s\\n' \"$$\" > worker.ready\nsleep 0.35\n: > worker.done\n",
    )
    .unwrap();

    // Normal outer exit must preserve a background service started under a redirected file.
    let mut service_outer = Command::new("/bin/sh");
    service_outer
        .current_dir(dir.path())
        .env("HOME", dir.path())
        .env("USERPROFILE", dir.path())
        .env("OPENVIKING_CLI_CONFIG_FILE", dir.path().join("ovcli.conf"))
        .env("OPENVIKING_LANG", "en")
        .args([
            "-c",
            r#""$1" privacy run -- /bin/sh -c '/bin/sh ./service.sh &' >service.log 2>&1; while [ ! -s service.pid ]; do sleep 0.02; done"#,
            "sh",
            env!("CARGO_BIN_EXE_ov"),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        service_outer.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let service_status = timeout(Duration::from_secs(5), service_outer.status())
        .await
        .expect("normal outer job did not finish")
        .unwrap();
    assert_eq!(service_status.code(), Some(0));
    let service_pid: i32 = fs::read_to_string(dir.path().join("service.pid"))
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    tokio::time::sleep(Duration::from_millis(150)).await;
    assert_eq!(
        posix::kill(service_pid, 0),
        0,
        "background service {service_pid} was killed after normal outer exit"
    );
    assert_eq!(posix::kill(service_pid, posix::SIGKILL), 0);
    for _ in 0..20 {
        if posix::kill(service_pid, 0) < 0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }

    let worker_ready = dir.path().join("worker.ready");
    let worker_done = dir.path().join("worker.done");
    let next_step = dir.path().join("next_step");

    let mut outer = Command::new("/bin/sh");
    outer
        .current_dir(dir.path())
        .env("HOME", dir.path())
        .env("USERPROFILE", dir.path())
        .env("OPENVIKING_CLI_CONFIG_FILE", dir.path().join("ovcli.conf"))
        .env("OPENVIKING_LANG", "en")
        .args([
            "-c",
            r#""$1" privacy run -- /bin/sh -c '/bin/sh ./worker.sh &' >job.log 2>&1; : > next_step; sleep 10; :"#,
            "sh",
            env!("CARGO_BIN_EXE_ov"),
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true);
    // Simulate a harness starting a multi-step shell script in its own process group.
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        outer.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut outer_child = outer.spawn().unwrap();
    let outer_pgid = outer_child.id().unwrap() as i32;

    timeout(Duration::from_secs(5), async {
        while fs::read_to_string(&worker_ready)
            .map(|text| text.trim().is_empty())
            .unwrap_or(true)
            || !next_step.exists()
        {
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    })
    .await
    .expect("outer script did not start redirected worker and advance");
    let worker_pid: i32 = fs::read_to_string(&worker_ready)
        .unwrap()
        .trim()
        .parse()
        .unwrap();
    assert_eq!(
        posix::kill(worker_pid, 0),
        0,
        "recorded worker {worker_pid} should still be alive before cancellation"
    );

    assert_eq!(posix::kill(-outer_pgid, posix::SIGKILL), 0);
    outer_child.wait().await.unwrap();

    for _ in 0..15 {
        if posix::kill(worker_pid, 0) < 0 {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert!(
        posix::kill(worker_pid, 0) < 0,
        "redirected background worker {worker_pid} outlived outer job"
    );
    tokio::time::sleep(Duration::from_millis(450)).await;
    assert!(!worker_done.exists());
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn group_signal_without_a_terminal_delivers_once_and_preserves_zero_status() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let mut command = privacy(dir.path(), "http://127.0.0.1:1");
    command
        .args([
            "run",
            "--",
            "/bin/sh",
            "-c",
            r#"
                count=0
                trap 'count=$((count + 1))' TERM
                echo ready
                while [ "$count" -eq 0 ]; do :; done
                sleep 0.1
                printf '%s\n' "$count"
                exit 0
            "#,
        ])
        .stdout(Stdio::piped());
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        command.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command.spawn().unwrap();

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
    .expect("group signal test did not finish");
}

#[cfg(unix)]
#[tokio::test]
async fn closing_stdout_delivers_sigpipe_without_error_output() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    for masked in [false, true] {
        let (url, server) = if masked {
            let (url, server) = resolver([(TOKEN, Some("secret-line".into()))]).await;
            (url, Some(server))
        } else {
            ("http://127.0.0.1:1".to_owned(), None)
        };
        let mut command = privacy(dir.path(), &url);
        command.arg("run");
        if masked {
            command.args(["--env", &format!("TOKEN={TOKEN}")]);
        }
        command
            .args([
                "--",
                "/bin/sh",
                "-c",
                r#"while :; do printf '%s\n' "${TOKEN:-plain-line}"; done"#,
            ])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().unwrap();
        let expected = if masked {
            format!("{TOKEN}\n")
        } else {
            "plain-line\n".to_owned()
        };

        timeout(Duration::from_secs(5), async {
            let mut stdout = BufReader::new(child.stdout.take().unwrap());
            let mut first = String::new();
            stdout.read_line(&mut first).await.unwrap();
            assert_eq!(first, expected);
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
        if let Some(server) = server {
            server.await.unwrap();
        }
    }
}

#[cfg(any(target_os = "linux", target_os = "macos"))]
#[tokio::test]
async fn preserves_regular_file_stdout_and_merged_stream_order() {
    use std::process::Stdio;

    let dir = tempfile::tempdir().unwrap();
    let out_path = dir.path().join("stdout.txt");
    let mut file_command = privacy(dir.path(), "http://127.0.0.1:1");
    file_command
        .args([
            "run",
            "--",
            "/bin/sh",
            "-c",
            "[ -f /dev/fd/1 ] && printf 'regular\\n' && printf 'err\\n' >&2",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::from(std::fs::File::create(&out_path).unwrap()))
        .stderr(Stdio::piped());
    // Detach from any terminal so the non-TTY file/pipe rules apply regardless of test runner.
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        file_command.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    // Use spawn + wait_with_output so tokio does not replace file stdout with a pipe.
    let output = file_command
        .spawn()
        .unwrap()
        .wait_with_output()
        .await
        .unwrap();
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(std::fs::read_to_string(&out_path).unwrap(), "regular\n");
    assert_eq!(output.stderr, b"err\n");

    for masked in [false, true] {
        for _ in 0..5 {
            let (url, server) = if masked {
                let (url, server) = resolver([(TOKEN, Some("secret-mid".into()))]).await;
                (url, Some(server))
            } else {
                ("http://127.0.0.1:1".to_owned(), None)
            };
            let (rx, tx) = std::io::pipe().unwrap();
            let mut merged = privacy(dir.path(), &url);
            merged.arg("run");
            if masked {
                merged.args(["--env", &format!("TOKEN={TOKEN}")]);
            }
            merged
                .args([
                    "--",
                    "/bin/sh",
                    "-c",
                    r#"printf '1\n'; printf '2\n' >&2; printf '3:%s\n' "${TOKEN:-plain}"; printf '4\n' >&2"#,
                ])
                .stdin(Stdio::null())
                .stdout(Stdio::from(tx.try_clone().unwrap()))
                .stderr(Stdio::from(tx));
            // SAFETY: setsid is async-signal-safe.
            unsafe {
                merged.pre_exec(|| {
                    if posix::setsid() < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    Ok(())
                });
            }
            let mut child = merged.spawn().unwrap();
            drop(merged);

            timeout(Duration::from_secs(5), async {
                let reader = tokio::task::spawn_blocking(move || {
                    let mut rx = rx;
                    let mut captured = String::new();
                    std::io::Read::read_to_string(&mut rx, &mut captured).unwrap();
                    captured
                });
                let (status, captured) = tokio::join!(child.wait(), reader);
                assert_eq!(status.unwrap().code(), Some(0));
                let expected = if masked {
                    format!("1\n2\n3:{TOKEN}\n4\n")
                } else {
                    "1\n2\n3:plain\n4\n".to_owned()
                };
                assert_eq!(captured.unwrap(), expected);
            })
            .await
            .expect("merged stream order test timed out");
            if let Some(server) = server {
                server.await.unwrap();
            }
        }
    }

    let separate_log = dir.path().join("separate.log");
    fs::write(&separate_log, "AGENT LOG HEADER\n").unwrap();
    let sep_out = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&separate_log)
        .unwrap();
    let sep_err = fs::OpenOptions::new()
        .append(true)
        .open(&separate_log)
        .unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-warn".into()))]).await;
    let mut separate = privacy(dir.path(), &url);
    separate
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
        .stdout(Stdio::from(sep_out))
        .stderr(Stdio::from(sep_err));
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        separate.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let status = separate.status().await.unwrap();
    assert_eq!(status.code(), Some(0));
    server.await.unwrap();
    assert_eq!(
        fs::read_to_string(&separate_log).unwrap(),
        format!("AGENT LOG HEADER\nwarn:{TOKEN}\n")
    );

    use std::io::{Seek, SeekFrom};
    let shared_log = dir.path().join("shared.log");
    fs::write(&shared_log, "AGENT LOG HEADER\n").unwrap();
    let mut shared_out = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open(&shared_log)
        .unwrap();
    shared_out.seek(SeekFrom::End(0)).unwrap();
    let shared_err = shared_out.try_clone().unwrap();
    let (url, server) = resolver([(TOKEN, Some("secret-shared".into()))]).await;
    let mut shared = privacy(dir.path(), &url);
    shared
        .args([
            "run",
            "--env",
            &format!("TOKEN={TOKEN}"),
            "--",
            "/bin/sh",
            "-c",
            r#"printf '1\n'; printf '2:%s\n' "$TOKEN" >&2"#,
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::from(shared_out))
        .stderr(Stdio::from(shared_err));
    // SAFETY: setsid is async-signal-safe.
    unsafe {
        shared.pre_exec(|| {
            if posix::setsid() < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let status = shared.status().await.unwrap();
    assert_eq!(status.code(), Some(0));
    server.await.unwrap();
    assert_eq!(
        fs::read_to_string(&shared_log).unwrap(),
        format!("AGENT LOG HEADER\n1\n2:{TOKEN}\n")
    );
}
