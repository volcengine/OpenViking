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
    let arg_references = if std::env::var_os("OPENVIKING_PRIVACY_RUN_STRICT")
        .is_some_and(|v| v == "1")
    {
        if command[1..]
            .iter()
            .any(|arg| reference_start(&arg.to_string_lossy()).is_some())
        {
            return Err(Error::Client(
                    "Privacy references in command arguments are disabled when OPENVIKING_PRIVACY_RUN_STRICT=1; pass them via --env or --env-file"
                        .into(),
                ));
        }
        Vec::new()
    } else {
        command[1..]
            .iter()
            .map(|arg| collect_arg_references(arg, &mut references))
            .collect::<Result<Vec<_>>>()?
    };

    // Resolve every reference before starting the child. The same snapshot masks its output.
    let values = if references.is_empty() {
        Vec::new()
    } else {
        resolve_references(&client, &references).await?
    };

    let mut child_command = Command::new(program);
    if arg_references.is_empty() {
        child_command.args(&command[1..]);
    } else {
        for (arg, spans) in command[1..].iter().zip(arg_references) {
            child_command.arg(replace_arg_references(arg, &spans, &values));
        }
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
    use std::os::fd::AsRawFd;
    use std::process::Stdio;
    use tokio::signal::unix::{SignalKind, signal};

    let mut interrupt = signal(SignalKind::interrupt())?;
    let mut terminate = signal(SignalKind::terminate())?;
    let mut hangup = signal(SignalKind::hangup())?;
    let caller_pgrp = unix_signal::getpgrp();
    let terminal = std::fs::File::open("/dev/tty").ok();
    let combined_rx = if masker.is_some() {
        if shared_non_tty_outputs() {
            let (tx, rx) = tokio::net::unix::pipe::pipe()?;
            // Clear O_NONBLOCK so the child inherits standard blocking descriptors.
            let write_fd = tx.into_blocking_fd()?;
            command.stdout(Stdio::from(write_fd.try_clone()?));
            command.stderr(Stdio::from(write_fd));
            Some(rx)
        } else {
            command.stdout(Stdio::piped()).stderr(Stdio::piped());
            None
        }
    } else {
        None
    };
    let mut child = spawn_child(&mut command)?;
    // Drop configured Stdio handles immediately so the parent holds no write end to combined_rx.
    drop(command);
    let pid = child.id().expect("just spawned") as i32;
    let wait = async {
        if let Some(masker) = masker {
            match combined_rx {
                Some(rx) => relay_stream(rx, tokio::io::stdout(), masker).await?,
                None => {
                    relay_child_output(child.stdout.take(), child.stderr.take(), masker).await?
                }
            }
        }
        child.wait().await.map(exit_code)
    };
    tokio::pin!(wait);
    loop {
        let sig = tokio::select! {
            biased;
            status = &mut wait => return Ok(status?),
            _ = interrupt.recv() => SignalKind::interrupt(),
            _ = terminate.recv() => SignalKind::terminate(),
            _ = hangup.recv() => SignalKind::hangup(),
        };
        // Skip forwarding SIGINT when ov leads its own group (setsid/killpg) or shares the
        // terminal's foreground job, where group SIGINT already reached the child once.
        if sig == SignalKind::interrupt()
            && (caller_pgrp == std::process::id() as i32
                || terminal
                    .as_ref()
                    .is_some_and(|tty| unix_signal::tcgetpgrp(tty.as_raw_fd()) == caller_pgrp))
        {
            continue;
        }
        unix_signal::kill(pid, sig.as_raw_value());
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
    let (stdout, stderr) = (child.stdout.take(), child.stderr.take());
    let output = async {
        match masker {
            Some(masker) => relay_child_output(stdout, stderr, masker).await,
            None => Ok(()),
        }
    };
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
    masker: &OutputMasker,
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
    masker: &OutputMasker,
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
    masker: &OutputMasker,
) -> std::io::Result<()> {
    let mut pending = Vec::new();
    let mut buffer = vec![0; 65536];
    loop {
        // Flush ready non-prefix output before awaiting more bytes, and batch consecutive ready reads.
        let size = match reader.read(&mut buffer).now_or_never() {
            Some(size) => size?,
            None => {
                writer.flush().await?;
                reader.read(&mut buffer).await?
            }
        };
        writer
            .write_all(&masker.mask(&mut pending, &buffer[..size], size == 0))
            .await?;
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
fn with_borrowed_file<T>(
    fd: impl std::os::fd::AsRawFd,
    f: impl FnOnce(&mut std::fs::File) -> std::io::Result<T>,
) -> Option<T> {
    use std::os::fd::FromRawFd;
    // SAFETY: ManuallyDrop borrows the open descriptor for `f` without closing it on return.
    let mut file =
        std::mem::ManuallyDrop::new(unsafe { std::fs::File::from_raw_fd(fd.as_raw_fd()) });
    f(&mut file).ok()
}

#[cfg(unix)]
fn shared_non_tty_outputs() -> bool {
    use std::io::IsTerminal;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{FileTypeExt, MetadataExt};

    if std::io::stdout().is_terminal() {
        return false;
    }
    let Some(out) = with_borrowed_file(std::io::stdout(), |f| f.metadata()) else {
        return false;
    };
    let Some(err) = with_borrowed_file(std::io::stderr(), |f| f.metadata()) else {
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
    use std::io::Seek;

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
    // SAFETY: F_GETFL (3) reads file status flags without modifying descriptor state.
    let flags = unsafe { fcntl(out_fd, 3) };
    if flags < 0 || flags != unsafe { fcntl(err_fd, 3) } {
        return false;
    }
    // Reading out_fd before and after err_fd tolerates monotonic concurrent writes to a shared file.
    let Some(p1) = with_borrowed_file(out_fd, |f| f.stream_position()) else {
        return false;
    };
    let Some(p2) = with_borrowed_file(err_fd, |f| f.stream_position()) else {
        return false;
    };
    let Some(p3) = with_borrowed_file(out_fd, |f| f.stream_position()) else {
        return false;
    };
    p1 <= p2 && p2 <= p3
}

#[cfg(unix)]
mod unix_signal {
    unsafe extern "C" {
        pub safe fn kill(pid: i32, sig: i32) -> i32;
        pub safe fn getpgrp() -> i32;
        pub safe fn tcgetpgrp(fd: i32) -> i32;
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
        let masker = OutputMasker::new([
            ("密".to_owned(), "<first>".to_owned()),
            ("密钥".to_owned(), "<long>".to_owned()),
            ("密".to_owned(), "<alias>".to_owned()),
            ("ababc".to_owned(), "<overlap>".to_owned()),
            ("aa".to_owned(), "<run>".to_owned()),
        ]);
        let mut input = vec![0xff];
        input.extend_from_slice("密-密钥-abababc-aaaaa-ab".as_bytes());
        let mut expected = vec![0xff];
        expected.extend_from_slice(b"<first>-<long>-ab<overlap>-<run><run>a-ab");

        for step in 1..=input.len() {
            let mut pending = Vec::new();
            let mut actual = Vec::new();
            for chunk in input.chunks(step) {
                actual.extend(masker.mask(&mut pending, chunk, false));
            }
            actual.extend(masker.mask(&mut pending, &[], true));
            assert_eq!(actual, expected, "step {step}");
        }
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
