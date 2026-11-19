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

fn spawn_child(command: &mut Command) -> Result<tokio::process::Child> {
    command
        .spawn()
        .map_err(|error| Error::Client(format!("Could not start the child command: {error}")))
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
    const F_GETFL: i32 = 3;

    // SAFETY: F_GETFL takes no variadic arguments and only reads descriptor status flags.
    let (out_flags, err_flags) = unsafe { (fcntl(out_fd, F_GETFL), fcntl(err_fd, F_GETFL)) };
    if out_flags < 0 || out_flags != err_flags {
        return false;
    }
    // Never call lseek(SEEK_CUR): XNU writes back fg_offset and races with concurrent writers.
    #[cfg(target_os = "macos")]
    {
        #[repr(C)]
        struct VnodeFdInfo {
            openflags: u32,
            status: u32,
            offset: i64,
            _vnode: [u8; 160],
        }
        unsafe extern "C" {
            fn proc_pidfdinfo(
                pid: i32,
                fd: i32,
                flavor: i32,
                buffer: *mut VnodeFdInfo,
                buffersize: i32,
            ) -> i32;
        }
        const PROC_PIDFDVNODEINFO: i32 = 1;
        const PROC_FP_SHARED: u32 = 1;

        let pid = std::process::id() as i32;
        let size = std::mem::size_of::<VnodeFdInfo>() as i32;
        let mut out_info = std::mem::MaybeUninit::<VnodeFdInfo>::zeroed();
        let mut err_info = std::mem::MaybeUninit::<VnodeFdInfo>::zeroed();
        // SAFETY: Both buffers match sizeof(struct vnode_fdinfo) (176 bytes on macOS) and are
        // read only when proc_pidfdinfo reports it filled the full struct.
        unsafe {
            if proc_pidfdinfo(
                pid,
                out_fd,
                PROC_PIDFDVNODEINFO,
                out_info.as_mut_ptr(),
                size,
            ) != size
                || proc_pidfdinfo(
                    pid,
                    err_fd,
                    PROC_PIDFDVNODEINFO,
                    err_info.as_mut_ptr(),
                    size,
                ) != size
            {
                return false;
            }
            let (out_info, err_info) = (out_info.assume_init(), err_info.assume_init());
            (out_info.status & PROC_FP_SHARED) != 0
                && (err_info.status & PROC_FP_SHARED) != 0
                && out_info.openflags == err_info.openflags
                && out_info.offset == err_info.offset
        }
    }
    #[cfg(target_os = "linux")]
    {
        let fd_pos = |fd: i32| {
            std::fs::read_to_string(format!("/proc/self/fdinfo/{fd}"))
                .ok()?
                .lines()
                .find_map(|line| line.strip_prefix("pos:"))
                .map(|value| value.trim().to_owned())
        };
        match (fd_pos(out_fd), fd_pos(err_fd)) {
            (Some(out_pos), Some(err_pos)) => out_pos == err_pos,
            _ => true,
        }
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        true
    }
}

#[cfg(unix)]
async fn run_child(mut command: Command, masker: Option<&OutputMasker>) -> Result<i32> {
    use std::io::{IsTerminal, Write};
    use std::os::fd::AsRawFd;
    use std::os::unix::process::CommandExt;
    use std::process::Stdio;
    use tokio::signal::unix::{SignalKind, signal};

    // POSIX functions from the C library that std already links; pid_t is i32 on Unix.
    unsafe extern "C" {
        safe fn kill(pid: i32, sig: i32) -> i32;
        safe fn getpgrp() -> i32;
        safe fn tcgetpgrp(fd: i32) -> i32;
    }
    const SIGKILL: i32 = 9;

    let mut interrupt = signal(SignalKind::interrupt())?;
    let mut terminate = signal(SignalKind::terminate())?;
    let mut hangup = signal(SignalKind::hangup())?;
    // With a terminal job, the child stays in the shell's group so it can read /dev/tty and
    // follow Ctrl-Z and fg. Otherwise, its own group lets ov signal the whole command once.
    let caller_pgrp = getpgrp();
    let job = caller_pgrp == std::process::id() as i32
        || std::io::stdin().is_terminal()
        || std::io::stdout().is_terminal()
        || std::io::stderr().is_terminal();
    let terminal = std::fs::File::open("/dev/tty").ok().filter(|_| job);
    let pipe_out = masker.is_some() || (terminal.is_none() && is_pipe_or_socket(std::io::stdout()));
    let pipe_err = masker.is_some() || (terminal.is_none() && is_pipe_or_socket(std::io::stderr()));
    // Unmasked non-terminal commands whose outputs go straight to files stay in the caller's
    // group so background services survive normal exit and group SIGKILL still reaches workers.
    let isolate = terminal.is_none() && (pipe_out || pipe_err);
    if isolate {
        command.process_group(0);
    }
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
    // A SIGKILL sent to ov's group cannot reach an isolated group, and ov cannot catch it.
    // This shell joins the command's group and ends it when ov exits without writing a newline.
    let guard = if !isolate {
        None
    } else {
        std::process::Command::new("/bin/sh")
            .args(["-c", "trap '' INT TERM HUP; read line || kill -s KILL 0"])
            .process_group(pid)
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .ok()
    };
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
                if status.is_err() && isolate {
                    kill(-pid, SIGKILL);
                }
                if let Some(mut guard) = guard {
                    if let Some(mut stdin) = guard.stdin.take() {
                        let _ = stdin.write_all(b"\n");
                    }
                    let _ = guard.wait();
                }
                return Ok(status?);
            }
            _ = interrupt.recv() => SignalKind::interrupt(),
            _ = terminate.recv() => SignalKind::terminate(),
            _ = hangup.recv() => SignalKind::hangup(),
        };
        let target = if isolate {
            -pid
        } else {
            // Ctrl-C reaches every process in the terminal's foreground job, the child included.
            // A SIGINT sent only to a foreground ov is therefore not forwarded.
            if let Some(tty) = &terminal
                && sig == SignalKind::interrupt()
                && tcgetpgrp(tty.as_raw_fd()) == caller_pgrp
            {
                continue;
            }
            pid
        };
        // A child that already exited is reported by the wait above.
        kill(target, sig.as_raw_value());
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
    let relay_out = async {
        if let Some(stdout) = stdout {
            relay_stream(stdout, tokio::io::stdout(), masker).await?;
        }
        std::io::Result::Ok(())
    };
    let relay_err = async {
        if let Some(stderr) = stderr {
            relay_stream(stderr, tokio::io::stderr(), masker).await?;
        }
        std::io::Result::Ok(())
    };
    tokio::try_join!(relay_out, relay_err)?;
    Ok(())
}

async fn relay_stream<R, W>(
    reader: R,
    writer: W,
    masker: Option<&OutputMasker>,
) -> std::io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    match relay_output(reader, writer, masker).await {
        // Like a shell pipeline (`| head`), closing a stream's reader stops that stream
        // so the child receives SIGPIPE on its next write while the other stream finishes.
        Err(error) if error.kind() == std::io::ErrorKind::BrokenPipe => Ok(()),
        result => result,
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

async fn relay_output<R, W>(
    mut reader: R,
    mut writer: W,
    masker: Option<&OutputMasker>,
) -> std::io::Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
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
