use crate::client::HttpClient;
use crate::error::{Error, Result};
use futures::{FutureExt, StreamExt, TryStreamExt, stream};
use std::collections::VecDeque;
use std::ffi::{OsStr, OsString};
use std::path::PathBuf;
use tokio::io::{AsyncRead, AsyncReadExt, AsyncWrite, AsyncWriteExt};
use tokio::process::Command;

const MAX_CONCURRENT_RESOLVES: usize = 4;

struct ArgReference {
    start: usize,
    end: usize,
    index: usize,
}

pub async fn run(
    client: impl std::ops::Deref<Target = HttpClient>,
    env: &[String],
    env_files: &[PathBuf],
    command: &[OsString],
) -> Result<i32> {
    let mut env_fields = Vec::new();
    for (index, path) in env_files.iter().enumerate() {
        let source = format!("--env-file #{}", index + 1);
        let content = std::fs::read_to_string(path)
            .map_err(|error| Error::Client(format!("Cannot read {source}: {error}")))?;
        for (line_number, line) in content.trim_start_matches('\u{feff}').lines().enumerate() {
            let line = line.trim();
            if !line.is_empty() && !line.starts_with('#') {
                add_env_field(
                    &mut env_fields,
                    line,
                    &format!("{source}, line {}", line_number + 1),
                )?;
            }
        }
    }
    for (index, field) in env.iter().enumerate() {
        add_env_field(&mut env_fields, field, &format!("--env #{}", index + 1))?;
    }
    let mut references = Vec::new();
    let env_fields: Vec<_> = env_fields
        .into_iter()
        .map(|(name, reference)| (name, remember_reference(&mut references, reference)))
        .collect();

    let program = command
        .first()
        .ok_or_else(|| Error::Client("A command is required".into()))?;
    if reference_start(&program.to_string_lossy()).is_some() {
        return Err(Error::Client(
            "The program name cannot contain a Privacy reference".into(),
        ));
    }
    // Keep spans in the original arguments so inserted values are never parsed again.
    let arg_references = command[1..]
        .iter()
        .map(|arg| collect_arg_references(arg, &mut references))
        .collect::<Result<Vec<_>>>()?;

    // Resolve every reference before starting the child. The same snapshot masks its output.
    let values = if references.is_empty() {
        Vec::new()
    } else {
        resolve_references(&client, &references).await?
    };

    let mut child_command = Command::new(program);
    for (arg, spans) in command[1..].iter().zip(arg_references) {
        child_command.arg(replace_arg_references(arg, &spans, &values));
    }
    for (name, index) in env_fields {
        child_command.env(name, &values[index]);
    }
    let masker =
        (!values.is_empty()).then(|| OutputMasker::new(values.into_iter().zip(references)));
    child_command.kill_on_drop(true);
    run_child(child_command, masker.as_ref()).await
}

async fn resolve_references(client: &HttpClient, references: &[String]) -> Result<Vec<String>> {
    stream::iter(references.iter().enumerate())
        .map(|(index, reference)| async move {
            let result = client
                .privacy_resolve_selector(reference)
                .await
                .map_err(|_| {
                    Error::Client(format!("Cannot resolve Privacy reference #{}", index + 1))
                })?;
            result
                .get("value")
                .and_then(|value| value.as_str())
                .filter(|value| !value.is_empty() && !value.contains('\0'))
                .map(str::to_string)
                .ok_or_else(|| {
                    Error::Client(format!(
                        "Privacy reference #{} must contain a nonempty string without NUL bytes",
                        index + 1
                    ))
                })
        })
        .buffered(MAX_CONCURRENT_RESOLVES)
        .try_collect::<Vec<_>>()
        .await
}

fn valid_env_name(name: &str) -> bool {
    let mut bytes = name.bytes();
    bytes
        .next()
        .is_some_and(|b| b.is_ascii_alphabetic() || b == b'_')
        && bytes.all(|b| b.is_ascii_alphanumeric() || b == b'_')
}

fn add_env_field(fields: &mut Vec<(String, String)>, entry: &str, source: &str) -> Result<()> {
    let (name, reference) = entry
        .split_once('=')
        .ok_or_else(|| Error::Client(format!("{source} expects NAME=reference")))?;
    let name = name.trim();
    let reference = reference.trim();
    let reference = reference
        .strip_prefix('"')
        .and_then(|value| value.strip_suffix('"'))
        .or_else(|| {
            reference
                .strip_prefix('\'')
                .and_then(|value| value.strip_suffix('\''))
        })
        .unwrap_or(reference);
    if !valid_env_name(name) {
        return Err(Error::Client(format!(
            "Invalid environment variable name in {source}"
        )));
    }
    let reference = next_reference(reference)?
        .filter(|(start, end, _)| *start == 0 && *end == reference.len())
        .map(|(_, _, canonical)| canonical)
        .ok_or_else(|| Error::Client(format!("{source} requires a Privacy field reference")))?;
    if let Some((_, current)) = fields.iter_mut().find(|(field, _)| field == name) {
        *current = reference;
    } else {
        fields.push((name.to_string(), reference));
    }
    Ok(())
}

fn remember_reference(references: &mut Vec<String>, reference: String) -> usize {
    if let Some(index) = references.iter().position(|item| item == &reference) {
        index
    } else {
        references.push(reference);
        references.len() - 1
    }
}

// The server accepts spaces and tabs immediately inside the braces.
fn reference_start(text: &str) -> Option<(usize, usize)> {
    text.match_indices("ov_privacy:").find_map(|(marker, _)| {
        let before = text[..marker].trim_end_matches([' ', '\t']);
        Some((before.strip_suffix("{{")?.len(), marker))
    })
}

fn next_reference(text: &str) -> Result<Option<(usize, usize, String)>> {
    let Some((start, marker)) = reference_start(text) else {
        return Ok(None);
    };
    let (body, _) = text[marker..]
        .split_once("}}")
        .ok_or_else(|| Error::Client("Incomplete Privacy reference".into()))?;
    let end = marker + body.len() + 2;
    let body = body.trim_end_matches([' ', '\t']);
    Ok(Some((start, end, format!("{{{{{body}}}}}"))))
}

fn collect_arg_references(arg: &OsStr, references: &mut Vec<String>) -> Result<Vec<ArgReference>> {
    let Some(text) = arg.to_str() else {
        if reference_start(&arg.to_string_lossy()).is_some() {
            return Err(Error::Client(
                "An argument containing a Privacy reference must be UTF-8".into(),
            ));
        }
        return Ok(Vec::new());
    };
    let mut spans = Vec::new();
    let mut cursor = 0;
    while let Some((start, end, reference)) = next_reference(&text[cursor..])? {
        let (start, end) = (cursor + start, cursor + end);
        let index = remember_reference(references, reference);
        spans.push(ArgReference { start, end, index });
        cursor = end;
    }
    Ok(spans)
}

fn replace_arg_references(arg: &OsStr, spans: &[ArgReference], values: &[String]) -> OsString {
    if spans.is_empty() {
        return arg.to_os_string();
    }
    let text = arg.to_str().expect("arguments with references are UTF-8");
    let mut output = String::with_capacity(text.len());
    let mut cursor = 0;
    for span in spans {
        output.push_str(&text[cursor..span.start]);
        output.push_str(&values[span.index]);
        cursor = span.end;
    }
    output.push_str(&text[cursor..]);
    output.into()
}

fn spawn_child(command: &mut Command) -> Result<tokio::process::Child> {
    command
        .spawn()
        .map_err(|error| Error::Client(format!("Could not start the child command: {error}")))
}

#[cfg(unix)]
async fn run_child(mut command: Command, masker: Option<&OutputMasker>) -> Result<i32> {
    use std::io::IsTerminal;
    use std::os::fd::AsRawFd;
    use std::process::Stdio;
    use tokio::signal::unix::{SignalKind, signal};

    let mut interrupt = signal(SignalKind::interrupt())?;
    let mut terminate = signal(SignalKind::terminate())?;
    let mut hangup = signal(SignalKind::hangup())?;
    // With a terminal job, the child stays in the shell's group so it can read /dev/tty and
    // follow Ctrl-Z and fg. Otherwise, its own group lets ov signal the whole command once.
    let caller_pgrp = unix_pgrp::getpgrp();
    let job = caller_pgrp == std::process::id() as i32
        || std::io::stdin().is_terminal()
        || std::io::stdout().is_terminal()
        || std::io::stderr().is_terminal();
    let terminal = std::fs::File::open("/dev/tty").ok().filter(|_| job);
    if terminal.is_none() {
        command.process_group(0);
    }
    let pipe_out = masker.is_some() || (terminal.is_none() && is_pipe_or_socket(std::io::stdout()));
    let pipe_err = masker.is_some() || (terminal.is_none() && is_pipe_or_socket(std::io::stderr()));
    let combined_rx = if pipe_out && pipe_err && shared_non_tty_outputs() {
        let (tx, rx) = tokio::net::unix::pipe::pipe()?;
        // Clear O_NONBLOCK so the child inherits standard blocking descriptors.
        let write_fd = tx.into_blocking_fd()?;
        command.stdout(Stdio::from(write_fd.try_clone()?));
        command.stderr(Stdio::from(write_fd));
        Some(rx)
    } else {
        if pipe_out {
            command.stdout(Stdio::piped());
        }
        if pipe_err {
            command.stderr(Stdio::piped());
        }
        None
    };
    let mut child = spawn_child(&mut command)?;
    // Drop configured Stdio handles immediately so the parent holds no write end to combined_rx.
    drop(command);
    let pid = child.id().expect("just spawned") as i32;
    let pipes = [
        combined_rx.as_ref().map_or(-1, AsRawFd::as_raw_fd),
        child.stdout.as_ref().map_or(-1, AsRawFd::as_raw_fd),
        child.stderr.as_ref().map_or(-1, AsRawFd::as_raw_fd),
    ];
    let guard = terminal
        .is_none()
        .then(|| unix_pgrp::spawn_guard(pid, pipes))
        .flatten();
    let wait = async {
        match combined_rx {
            Some(rx) => relay_stream(rx, tokio::io::stdout(), masker).await?,
            None => relay_child_output(child.stdout.take(), child.stderr.take(), masker).await?,
        }
        child.wait().await.map(exit_code)
    };
    tokio::pin!(wait);
    loop {
        let sig = tokio::select! {
            biased;
            status = &mut wait => {
                if terminal.is_none() {
                    if status.is_err() {
                        unix_pgrp::kill(-pid, unix_pgrp::SIGKILL);
                    } else {
                        for (raw, rx) in [
                            (SignalKind::interrupt().as_raw_value(), &mut interrupt),
                            (SignalKind::terminate().as_raw_value(), &mut terminate),
                            (SignalKind::hangup().as_raw_value(), &mut hangup),
                        ] {
                            if rx.recv().now_or_never().is_some() {
                                unix_pgrp::kill(-pid, raw);
                            }
                        }
                    }
                }
                if let Some(guard) = guard {
                    unix_pgrp::finish_guard(guard, pid, status.is_ok());
                }
                return Ok(status?);
            }
            _ = interrupt.recv() => SignalKind::interrupt(),
            _ = terminate.recv() => SignalKind::terminate(),
            _ = hangup.recv() => SignalKind::hangup(),
        };
        let target = match &terminal {
            None => -pid,
            // Ctrl-C reaches every process in the terminal's foreground job, the child included.
            // A SIGINT sent only to a foreground ov is therefore not forwarded.
            Some(tty)
                if sig == SignalKind::interrupt()
                    && unix_pgrp::tcgetpgrp(tty.as_raw_fd()) == caller_pgrp =>
            {
                continue;
            }
            Some(_) => pid,
        };
        // A child that already exited is reported by the wait above.
        unix_pgrp::kill(target, sig.as_raw_value());
    }
}

#[cfg(not(unix))]
async fn run_child(mut command: Command, masker: Option<&OutputMasker>) -> Result<i32> {
    if masker.is_some() {
        command
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
    }
    let mut child = spawn_child(&mut command)?;
    drop(command);
    let output = relay_child_output(child.stdout.take(), child.stderr.take(), masker);
    let wait = async {
        tokio::select! {
            status = child.wait() => status.map(exit_code),
            _ = tokio::signal::ctrl_c() => {
                child.kill().await?;
                Ok(130)
            }
        }
    };
    let (_, code) = tokio::try_join!(output, wait)?;
    Ok(code)
}

async fn relay_child_output(
    stdout: Option<tokio::process::ChildStdout>,
    stderr: Option<tokio::process::ChildStderr>,
    masker: Option<&OutputMasker>,
) -> std::io::Result<()> {
    let out = async {
        match stdout {
            Some(s) => relay_stream(s, tokio::io::stdout(), masker).await,
            None => Ok(()),
        }
    };
    let err = async {
        match stderr {
            Some(s) => relay_stream(s, tokio::io::stderr(), masker).await,
            None => Ok(()),
        }
    };
    tokio::try_join!(out, err).map(|_| ())
}

async fn relay_stream<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    reader: R,
    writer: W,
    masker: Option<&OutputMasker>,
) -> std::io::Result<()> {
    match relay_output(reader, writer, masker).await {
        // Like a shell pipeline (`| head`), closing a stream's reader stops that stream
        // so the child receives SIGPIPE on its next write while the other stream finishes.
        Err(error) if error.kind() == std::io::ErrorKind::BrokenPipe => Ok(()),
        result => result,
    }
}

async fn relay_output<R: AsyncRead + Unpin, W: AsyncWrite + Unpin>(
    mut reader: R,
    mut writer: W,
    masker: Option<&OutputMasker>,
) -> std::io::Result<()> {
    let mut pending = Vec::new();
    let mut buffer = vec![0; 65536];
    loop {
        // Flush prompts before waiting for more input, and batch consecutive ready reads.
        let size = match reader.read(&mut buffer).now_or_never() {
            Some(size) => size?,
            None => {
                writer.flush().await?;
                reader.read(&mut buffer).await?
            }
        };
        if let Some(masker) = masker {
            writer
                .write_all(&masker.mask(&mut pending, &buffer[..size], size == 0))
                .await?;
        } else if size > 0 {
            writer.write_all(&buffer[..size]).await?;
        }
        if size == 0 {
            return writer.flush().await;
        }
    }
}

fn exit_code(status: std::process::ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(1))
    }
    #[cfg(not(unix))]
    {
        status.code().unwrap_or(1)
    }
}

#[cfg(unix)]
fn fd_metadata(fd: impl std::os::fd::AsFd) -> Option<std::fs::Metadata> {
    // Duplicate before wrapping in File so dropping it does not close the caller's descriptor.
    let duplicated = fd.as_fd().try_clone_to_owned().ok()?;
    std::fs::File::from(duplicated).metadata().ok()
}

#[cfg(unix)]
fn is_pipe_or_socket(fd: impl std::os::fd::AsFd) -> bool {
    use std::os::unix::fs::FileTypeExt;

    fd_metadata(fd).is_some_and(|meta| {
        let kind = meta.file_type();
        kind.is_fifo() || kind.is_socket()
    })
}

#[cfg(unix)]
fn shared_non_tty_outputs() -> bool {
    use std::io::IsTerminal;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{FileTypeExt, MetadataExt};

    if std::io::stdout().is_terminal() {
        return false;
    }
    let Some(out) = fd_metadata(std::io::stdout()) else {
        return false;
    };
    let Some(err) = fd_metadata(std::io::stderr()) else {
        return false;
    };
    if out.ino() == 0 || out.dev() != err.dev() || out.ino() != err.ino() {
        return false;
    }
    let kind = out.file_type();
    if kind.is_fifo() || kind.is_socket() {
        return true;
    }
    kind.is_file()
        && same_regular_file_description(
            std::io::stdout().as_raw_fd(),
            std::io::stderr().as_raw_fd(),
        )
}

#[cfg(unix)]
fn same_regular_file_description(out_fd: i32, err_fd: i32) -> bool {
    unsafe extern "C" {
        fn fcntl(fd: i32, cmd: i32, ...) -> i32;
    }
    if out_fd == err_fd {
        return true;
    }
    #[cfg(target_os = "linux")]
    {
        // SAFETY: F_DUPFD_QUERY (Linux >= 6.10) queries struct file equality without side effects.
        let equal = unsafe { fcntl(out_fd, 1027, err_fd) };
        if equal >= 0 {
            return equal == 1;
        }
        #[cfg(any(target_arch = "x86_64", target_arch = "aarch64"))]
        {
            unsafe extern "C" {
                fn syscall(num: std::ffi::c_long, ...) -> std::ffi::c_long;
            }
            const SYS_KCMP: std::ffi::c_long = if cfg!(target_arch = "x86_64") {
                312
            } else {
                272
            };
            let pid = std::process::id() as std::ffi::c_long;
            // SAFETY: KCMP_FILE (0) compares two descriptors in this process without mutating state.
            let cmp = unsafe {
                syscall(
                    SYS_KCMP,
                    pid,
                    pid,
                    0 as std::ffi::c_long,
                    out_fd as std::ffi::c_ulong,
                    err_fd as std::ffi::c_ulong,
                )
            };
            if cmp >= 0 {
                return cmp == 0;
            }
        }
    }
    #[cfg(target_os = "macos")]
    {
        #[repr(C)]
        #[derive(Clone, Copy)]
        struct Flock {
            l_start: i64,
            l_len: i64,
            l_pid: i32,
            l_type: i16,
            l_whence: i16,
        }
        let mut lock = Flock {
            l_start: (1_i64 << 62) - i64::from(std::process::id()),
            l_len: 1,
            l_pid: 0,
            l_type: 3, // F_WRLCK
            l_whence: 0,
        };
        // SAFETY: F_OFD_SETLK (90) / F_OFD_GETLK (92) probe a PID-unique byte on struct fileglob.
        if unsafe { fcntl(out_fd, 90, &mut lock) } == 0 {
            let mut query = lock;
            let shared = unsafe { fcntl(err_fd, 92, &mut query) } == 0 && query.l_type == 2;
            lock.l_type = 2; // F_UNLCK
            unsafe { fcntl(out_fd, 90, &mut lock) };
            return shared;
        }
    }
    #[cfg(any(target_os = "linux", target_os = "macos"))]
    {
        #[cfg(target_os = "linux")]
        let (get_cmd, set_cmd, alt) = (9, 8, std::process::id() as i32); // F_GETOWN / F_SETOWN
        #[cfg(target_os = "macos")]
        let (get_cmd, set_cmd, alt) = (74, 73, 1); // F_GETNOSIGPIPE / F_SETNOSIGPIPE

        // SAFETY: F_GETFL (3) and get_cmd read descriptor state; set_cmd toggles and restores a
        // per-open-file-description attribute without touching file offsets or regular-file I/O.
        unsafe {
            let flags = fcntl(out_fd, 3);
            if flags < 0 || flags != fcntl(err_fd, 3) {
                return false;
            }
            let orig = fcntl(out_fd, get_cmd);
            if orig != fcntl(err_fd, get_cmd) {
                return false;
            }
            let probe = if orig == alt { 0 } else { alt };
            if fcntl(out_fd, set_cmd, probe) < 0 {
                return false;
            }
            let shared = fcntl(err_fd, get_cmd) == probe;
            fcntl(out_fd, set_cmd, orig);
            shared
        }
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        let _ = fcntl as unsafe extern "C" fn(i32, i32, ...) -> i32;
        false
    }
}

#[cfg(any(target_os = "linux", test))]
fn parse_proc_stat(stat: &[u8]) -> Option<(u8, i32)> {
    let end = stat.windows(2).rposition(|w| w == b") ")?;
    let (&state, rest) = stat.get(end + 2..)?.split_first()?;
    let rest = rest.strip_prefix(b" ")?;
    let pgrp = rest.get(rest.iter().position(|&b| b == b' ')? + 1..)?;
    let len = pgrp
        .iter()
        .position(|&b| b == b' ' || b == b'\n')
        .unwrap_or(pgrp.len());
    Some((
        state,
        std::str::from_utf8(pgrp.get(..len)?).ok()?.parse().ok()?,
    ))
}

#[cfg(unix)]
mod unix_pgrp {
    use std::ffi::{c_char, c_long, c_void};
    use std::io::{PipeWriter, Write};
    use std::os::fd::AsRawFd;
    use std::sync::atomic::{AtomicU32, Ordering};

    pub const SIGKILL: i32 = 9;
    const SIGPIPE: i32 = 13;
    const TRACKED_SIGNALS: [i32; 4] = [1, 2, 3, 15]; // SIGHUP, SIGINT, SIGQUIT, SIGTERM

    #[repr(C)]
    struct Timespec {
        tv_sec: c_long,
        tv_nsec: c_long,
    }

    unsafe extern "C" {
        pub safe fn kill(pid: i32, sig: i32) -> i32;
        pub safe fn getpgrp() -> i32;
        pub safe fn tcgetpgrp(fd: i32) -> i32;
        fn fork() -> i32;
        safe fn setpgid(pid: i32, pgid: i32) -> i32;
        fn waitpid(pid: i32, status: *mut i32, options: i32) -> i32;
        fn open(path: *const c_char, oflag: i32, ...) -> i32;
        safe fn close(fd: i32) -> i32;
        safe fn dup2(oldfd: i32, newfd: i32) -> i32;
        fn fcntl(fd: i32, cmd: i32, ...) -> i32;
        fn read(fd: i32, buf: *mut c_void, count: usize) -> isize;
        fn write(fd: i32, buf: *const c_void, count: usize) -> isize;
        fn signal(sig: i32, handler: usize) -> usize;
        fn nanosleep(req: *const Timespec, rem: *mut Timespec) -> i32;
        safe fn _exit(status: i32) -> !;
    }

    static SIGNALS: AtomicU32 = AtomicU32::new(0);

    extern "C" fn record_signal(sig: i32) {
        if (1..32).contains(&sig) {
            SIGNALS.fetch_or(1u32 << sig, Ordering::Relaxed);
        }
    }

    fn retry_eintr(mut op: impl FnMut() -> isize) -> isize {
        loop {
            let rc = op();
            if rc >= 0 || std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted
            {
                return rc;
            }
        }
    }

    fn detach_stdio(mut keep_fd: i32) -> i32 {
        if (0..=2).contains(&keep_fd) {
            // SAFETY: F_DUPFD (0) duplicates keep_fd to the lowest free descriptor >= 3.
            let moved = unsafe { fcntl(keep_fd, 0, 3) };
            if moved >= 3 {
                close(keep_fd);
                keep_fd = moved;
            }
        }
        // SAFETY: c"/dev/null" is NUL-terminated; O_RDWR (2) takes no mode argument.
        let null_fd = unsafe { open(c"/dev/null".as_ptr(), 2) };
        if null_fd >= 0 {
            for fd in 0..=2 {
                if fd != keep_fd {
                    dup2(null_fd, fd);
                }
            }
            if null_fd > 2 && null_fd != keep_fd {
                close(null_fd);
            }
        }
        keep_fd
    }

    fn group_alive(pgid: i32) -> bool {
        (kill(-pgid, 0) == 0
            || std::io::Error::last_os_error().kind() == std::io::ErrorKind::PermissionDenied)
            && linux_group_alive(pgid)
    }

    #[cfg(not(all(
        target_os = "linux",
        any(target_arch = "x86_64", target_arch = "aarch64")
    )))]
    fn linux_group_alive(_pgid: i32) -> bool {
        true
    }

    #[cfg(all(
        target_os = "linux",
        any(target_arch = "x86_64", target_arch = "aarch64")
    ))]
    fn linux_group_alive(pgid: i32) -> bool {
        unsafe extern "C" {
            fn openat(dirfd: i32, path: *const c_char, oflag: i32, ...) -> i32;
            fn syscall(num: c_long, ...) -> c_long;
        }
        const SYS_GETDENTS64: c_long = if cfg!(target_arch = "x86_64") {
            217
        } else {
            61
        };
        // SAFETY: Uses only stack buffers and async-signal-safe syscalls after fork().
        unsafe {
            let proc_fd = open(c"/proc".as_ptr(), 0);
            if proc_fd < 0 {
                return true;
            }
            let (mut buf, mut alive) = ([0u8; 4096], false);
            while !alive {
                let n = syscall(
                    SYS_GETDENTS64,
                    proc_fd as c_long,
                    buf.as_mut_ptr(),
                    buf.len(),
                );
                if n <= 0 {
                    break;
                }
                let mut rest = &buf[..n as usize];
                while rest.len() > 19 {
                    let reclen = u16::from_ne_bytes([rest[16], rest[17]]) as usize;
                    let Some((rec, tail)) = (reclen >= 20)
                        .then(|| rest.split_at_checked(reclen))
                        .flatten()
                    else {
                        break;
                    };
                    rest = tail;
                    let name = &rec[19..];
                    let len = name.iter().position(|&b| b == 0).unwrap_or(0);
                    if (1..=20).contains(&len) && name[..len].iter().all(u8::is_ascii_digit) {
                        let mut path = [0u8; 28];
                        path[..len].copy_from_slice(&name[..len]);
                        path[len..len + 6].copy_from_slice(b"/stat\0");
                        let fd = openat(proc_fd, path.as_ptr().cast(), 0);
                        if fd >= 0 {
                            let mut stat = [0u8; 256];
                            let m = read(fd, stat.as_mut_ptr().cast(), stat.len());
                            close(fd);
                            if m > 0
                                && super::parse_proc_stat(&stat[..m as usize]).is_some_and(
                                    |(st, pg)| pg == pgid && !matches!(st, b'Z' | b'X' | b'x'),
                                )
                            {
                                alive = true;
                                break;
                            }
                        }
                    }
                }
            }
            close(proc_fd);
            alive
        }
    }

    pub fn spawn_guard(target_pid: i32, close_fds: [i32; 3]) -> Option<(i32, PipeWriter)> {
        let (rx, tx) = std::io::pipe().ok()?;
        let (rx_fd, tx_fd) = (rx.as_raw_fd(), tx.as_raw_fd());
        // SAFETY: Child uses only async-signal-safe syscalls before _exit(0).
        let pid = unsafe { fork() };
        if pid < 0 {
            return None;
        }
        if pid == 0 {
            setpgid(0, 0);
            close(tx_fd);
            for fd in close_fds {
                if fd >= 0 && fd != rx_fd {
                    close(fd);
                }
            }
            let rx_fd = detach_stdio(rx_fd);
            // SAFETY: SIG_IGN (1) is valid for non-fatal job signals and SIGPIPE.
            unsafe {
                signal(SIGPIPE, 1);
                for sig in TRACKED_SIGNALS {
                    signal(sig, 1);
                }
            }
            let mut byte = 0u8;
            // SAFETY: &raw mut byte points to a 1-byte stack slot.
            let disarmed = retry_eintr(|| unsafe { read(rx_fd, (&raw mut byte).cast(), 1) }) == 1
                && byte == b'D';
            close(rx_fd);
            if !disarmed {
                kill(-target_pid, SIGKILL);
            }
            _exit(0);
        }
        setpgid(pid, pid);
        drop(rx);
        Some((pid, tx))
    }

    pub fn finish_guard((guard_pid, mut tx): (i32, PipeWriter), target_pid: i32, clean_exit: bool) {
        if clean_exit && group_alive(target_pid) && handoff_sentinel(tx.as_raw_fd(), target_pid) {
            return;
        }
        let _ = tx.write_all(b"D");
        drop(tx);
        // SAFETY: null status ignores child exit status.
        retry_eintr(|| unsafe { waitpid(guard_pid, std::ptr::null_mut(), 0) } as isize);
    }

    fn handoff_sentinel(tx_fd: i32, target_pid: i32) -> bool {
        // SAFETY: Child uses only async-signal-safe syscalls before _exit(0).
        let pid = unsafe { fork() };
        if pid != 0 {
            return pid > 0;
        }
        SIGNALS.store(0, Ordering::Relaxed);
        // SAFETY: SIG_IGN (1) and record_signal (async-signal-safe) are valid handlers.
        unsafe {
            signal(SIGPIPE, 1);
            for sig in TRACKED_SIGNALS {
                signal(sig, record_signal as *const () as usize);
            }
        }
        let tx_fd = detach_stdio(tx_fd);
        let pause = Timespec {
            tv_sec: 0,
            tv_nsec: 50_000_000,
        };
        loop {
            let pending = SIGNALS.swap(0, Ordering::Relaxed);
            for sig in TRACKED_SIGNALS {
                if (pending & (1u32 << sig)) != 0 {
                    kill(-target_pid, sig);
                }
            }
            if !group_alive(target_pid) {
                // SAFETY: c"D" points to a valid 1-byte static buffer.
                retry_eintr(|| unsafe { write(tx_fd, c"D".as_ptr().cast(), 1) });
                close(tx_fd);
                _exit(0);
            }
            if SIGNALS.load(Ordering::Relaxed) == 0 {
                // SAFETY: pause is valid and null ignores remaining time on EINTR.
                unsafe { nanosleep(&pause, std::ptr::null_mut()) };
            }
        }
    }
}

/// Aho-Corasick trie shared by both output streams. Each terminal stores the
/// first reference for its value. Each stream keeps its own unfinished prefix.
struct OutputMasker {
    nodes: Vec<MatchNode>,
    root_edges: [usize; 256],
}

#[derive(Default)]
struct MatchNode {
    edges: Vec<(u8, usize)>,
    failure: usize,
    suffix_match: usize,
    depth: usize,
    reference: Option<String>,
}

impl MatchNode {
    fn child(&self, byte: u8) -> Option<usize> {
        self.edges
            .iter()
            .find_map(|&(edge, child)| (edge == byte).then_some(child))
    }
}

impl OutputMasker {
    fn new(replacements: impl IntoIterator<Item = (String, String)>) -> Self {
        let mut trie = Self {
            nodes: vec![MatchNode::default()],
            root_edges: [0; 256],
        };
        for (value, reference) in replacements {
            let mut node = 0;
            for byte in value.bytes() {
                node = match trie.nodes[node].child(byte) {
                    Some(child) => child,
                    None => {
                        let child = trie.nodes.len();
                        let depth = trie.nodes[node].depth + 1;
                        trie.nodes[node].edges.push((byte, child));
                        trie.nodes.push(MatchNode {
                            depth,
                            ..Default::default()
                        });
                        child
                    }
                };
            }
            if node != 0 {
                trie.nodes[node].reference.get_or_insert(reference);
            }
        }
        let mut queue = VecDeque::new();
        for &(byte, child) in &trie.nodes[0].edges {
            trie.root_edges[usize::from(byte)] = child;
            queue.push_back(child);
        }
        while let Some(node) = queue.pop_front() {
            for (byte, child) in trie.nodes[node].edges.clone() {
                let fallback = trie.advance(trie.nodes[node].failure, byte);
                trie.nodes[child].failure = fallback;
                trie.nodes[child].suffix_match = if trie.nodes[fallback].reference.is_some() {
                    fallback
                } else {
                    trie.nodes[fallback].suffix_match
                };
                queue.push_back(child);
            }
        }
        trie
    }

    fn advance(&self, mut node: usize, byte: u8) -> usize {
        while node != 0 {
            if let Some(child) = self.nodes[node].child(byte) {
                return child;
            }
            node = self.nodes[node].failure;
        }
        self.root_edges[usize::from(byte)]
    }

    fn mask(&self, pending: &mut Vec<u8>, chunk: &[u8], finish: bool) -> Vec<u8> {
        pending.extend_from_slice(chunk);
        // Scanning from left to right visits longer matches at the same start last.
        // Node zero is the root and never represents a match.
        let mut longest = Vec::new();
        let mut node = 0;
        let mut i = 0;
        while i < pending.len() {
            let byte = pending[i];
            let next = self.advance(node, byte);
            i += 1;
            if next == 0 {
                node = 0;
                continue;
            }
            let previous = node;
            node = next;
            let mut matched = if self.nodes[node].reference.is_some() {
                node
            } else {
                self.nodes[node].suffix_match
            };
            // Repeating a transition back to the same state yields the same matches.
            if matched == 0 && previous == node {
                let rest = &pending[i..];
                i += rest.iter().position(|&b| b != byte).unwrap_or(rest.len());
                continue;
            }
            while matched != 0 {
                if longest.is_empty() {
                    longest.resize(pending.len(), 0);
                }
                longest[i - self.nodes[matched].depth] = matched;
                matched = self.nodes[matched].suffix_match;
            }
        }
        // A leaf cannot grow. Its longest extendable suffix still can.
        while node != 0 && self.nodes[node].edges.is_empty() {
            node = self.nodes[node].failure;
        }
        let end = if finish {
            pending.len()
        } else {
            pending.len() - self.nodes[node].depth
        };
        if longest.is_empty() {
            return if end == pending.len() {
                std::mem::take(pending)
            } else {
                pending.drain(..end).collect()
            };
        }
        let mut output = Vec::with_capacity(end);
        let mut cursor = 0;
        while cursor < end {
            let Some(offset) = longest[cursor..end].iter().position(|&n| n != 0) else {
                output.extend_from_slice(&pending[cursor..end]);
                cursor = end;
                break;
            };
            let start = cursor + offset;
            let matched = &self.nodes[longest[start]];
            output.extend_from_slice(&pending[cursor..start]);
            output.extend_from_slice(
                matched
                    .reference
                    .as_ref()
                    .expect("terminal node")
                    .as_bytes(),
            );
            cursor = start + matched.depth;
        }
        pending.drain(..cursor);
        output
    }
}

#[cfg(test)]
mod tests {
    use super::OutputMasker;

    #[test]
    fn streaming_mask_matches_longest_value_reference() {
        fn next(state: &mut u64) -> usize {
            *state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            (*state >> 32) as usize
        }

        let alphabet = b"abXY09-";
        for seed in 0_u64..2000 {
            let mut state = seed + 1;
            let mut replacements = Vec::new();
            if seed.is_multiple_of(2) {
                replacements.extend([
                    ("密".to_owned(), "<first>".to_owned()),
                    ("密钥".to_owned(), "<long>".to_owned()),
                    ("密".to_owned(), "<alias>".to_owned()),
                ]);
            }
            for index in 0..1 + next(&mut state) % 8 {
                let value: String = (0..1 + next(&mut state) % 10)
                    .map(|_| alphabet[next(&mut state) % alphabet.len()] as char)
                    .collect();
                replacements.push((value, format!("<ref{index}>")));
            }
            let mut input = vec![0xff];
            for _ in 0..100 {
                if next(&mut state).is_multiple_of(5) {
                    let index = next(&mut state) % replacements.len();
                    input.extend_from_slice(replacements[index].0.as_bytes());
                } else {
                    input.push(alphabet[next(&mut state) % alphabet.len()]);
                }
            }
            let masker = OutputMasker::new(replacements.clone());
            // Stable sorting keeps the first reference when values are equal.
            replacements.sort_by_key(|(value, _)| std::cmp::Reverse(value.len()));
            let mut expected = Vec::new();
            let mut rest = input.as_slice();
            while let Some((&first, tail)) = rest.split_first() {
                if let Some((value, reference)) = replacements
                    .iter()
                    .find(|(value, _)| rest.starts_with(value.as_bytes()))
                {
                    expected.extend_from_slice(reference.as_bytes());
                    rest = &rest[value.len()..];
                } else {
                    expected.push(first);
                    rest = tail;
                }
            }

            let mut pending = Vec::new();
            let mut actual = Vec::new();
            for chunk in input.chunks(1 + next(&mut state) % 17) {
                actual.extend(masker.mask(&mut pending, chunk, false));
            }
            actual.extend(masker.mask(&mut pending, &[], true));
            assert_eq!(actual, expected, "seed {seed}");
        }
    }

    #[test]
    fn parses_proc_stat_across_comm_shapes() {
        assert_eq!(
            super::parse_proc_stat(b"42 (sh) S 1 42 40 0 -1\n"),
            Some((b'S', 42))
        );
        assert_eq!(
            super::parse_proc_stat(b"42 (sh (worker) ) Z 1 777 700 0 -1\n"),
            Some((b'Z', 777))
        );
        assert_eq!(
            super::parse_proc_stat(b"99 (done) X 1 888 800\n"),
            Some((b'X', 888))
        );
        assert_eq!(super::parse_proc_stat(b"malformed"), None);
    }

    #[cfg(unix)]
    #[tokio::test]
    async fn local_command_returns_signal_status_without_building_an_http_client() {
        let status = super::run(
            std::sync::LazyLock::new(|| {
                panic!("a local command must not initialize an HTTP client")
            }),
            &[],
            &[],
            &["/bin/sh".into(), "-c".into(), "kill -TERM $$".into()],
        )
        .await
        .unwrap();
        assert_eq!(status, 143);
    }
}
