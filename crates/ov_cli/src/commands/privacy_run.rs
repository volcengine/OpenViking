use crate::client::HttpClient;
use crate::error::{Error, Result};
use futures::{StreamExt, TryStreamExt, stream};
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
    let mask_output = !values.is_empty();
    child_command
        .stdin(std::process::Stdio::inherit())
        .kill_on_drop(true);
    if mask_output {
        child_command
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped());
    }
    let mut child = spawn_child(&mut child_command)?;
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let wait_task = async {
        tokio::select! {
            status = child.wait() => Ok((status?, false)),
            _ = tokio::signal::ctrl_c() => {
                child.start_kill()?;
                Ok((child.wait().await?, true))
            }
        }
    };
    let (status, interrupted) = if mask_output {
        let stdout = stdout.expect("stdout is piped");
        let stderr = stderr.expect("stderr is piped");
        let replacements: Vec<_> = values.into_iter().zip(references).collect();
        let stdout_task = relay_output(stdout, tokio::io::stdout(), replacements.clone());
        let stderr_task = relay_output(stderr, tokio::io::stderr(), replacements);
        let (_, _, status) = tokio::try_join!(stdout_task, stderr_task, wait_task)?;
        status
    } else {
        wait_task.await?
    };
    Ok(if interrupted { 130 } else { exit_code(status) })
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
    replacements: Vec<(String, String)>,
) -> Result<()>
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut masker = OutputMasker::new(replacements);
    let mut buffer = [0; 8192];
    loop {
        let size = reader.read(&mut buffer).await?;
        writer
            .write_all(&masker.mask(&buffer[..size], size == 0))
            .await?;
        if size == 0 {
            return Ok(writer.flush().await?);
        }
    }
}

/// Masks the values resolved for one command, including values split across reads.
struct OutputMasker {
    matcher: ValueMatcher,
    references: Vec<Vec<u8>>,
    pending: Vec<u8>,
    longest_value: usize,
}

#[derive(Default)]
struct MatchNode {
    children: Vec<(u8, usize)>,
    failure: usize,
    output_link: Option<usize>,
    patterns: Vec<usize>,
}

impl MatchNode {
    #[inline]
    fn child(&self, byte: u8) -> Option<usize> {
        match self.children.as_slice() {
            [] => None,
            [(edge, child)] => (*edge == byte).then_some(*child),
            children => children
                .iter()
                .find_map(|&(edge, child)| (edge == byte).then_some(child)),
        }
    }
}

struct ValueMatcher {
    nodes: Vec<MatchNode>,
    lengths: Vec<usize>,
    single: Option<(Vec<u8>, Vec<usize>)>,
}

impl ValueMatcher {
    fn new(patterns: &[String]) -> Self {
        if let [pattern] = patterns {
            let pattern = pattern.as_bytes().to_vec();
            let mut prefixes = vec![0; pattern.len()];
            let mut border = 0;
            for index in 1..pattern.len() {
                while border > 0 && pattern[index] != pattern[border] {
                    border = prefixes[border - 1];
                }
                if pattern[index] == pattern[border] {
                    border += 1;
                }
                prefixes[index] = border;
            }
            return Self {
                nodes: Vec::new(),
                lengths: vec![pattern.len()],
                single: Some((pattern, prefixes)),
            };
        }
        let mut nodes = vec![MatchNode::default()];
        for (index, pattern) in patterns.iter().enumerate() {
            let mut state = 0;
            for byte in pattern.bytes() {
                state = match nodes[state].child(byte) {
                    Some(child) => child,
                    None => {
                        let child = nodes.len();
                        nodes.push(MatchNode::default());
                        nodes[state].children.push((byte, child));
                        child
                    }
                };
            }
            nodes[state].patterns.push(index);
        }

        let mut queue: VecDeque<_> = nodes[0].children.iter().map(|&(_, child)| child).collect();
        while let Some(state) = queue.pop_front() {
            for (byte, child) in nodes[state].children.clone() {
                let mut failure = nodes[state].failure;
                while failure != 0 && nodes[failure].child(byte).is_none() {
                    failure = nodes[failure].failure;
                }
                let failure = nodes[failure].child(byte).unwrap_or(0);
                nodes[child].failure = failure;
                nodes[child].output_link = if nodes[failure].patterns.is_empty() {
                    nodes[failure].output_link
                } else {
                    Some(failure)
                };
                queue.push_back(child);
            }
        }
        Self {
            nodes,
            lengths: patterns.iter().map(String::len).collect(),
            single: None,
        }
    }

    fn matches(&self, input: &[u8], safe_end: usize) -> Vec<Option<usize>> {
        let mut best = vec![None; safe_end];
        if let Some((pattern, prefixes)) = &self.single {
            let mut matched = 0;
            for (end, &byte) in input.iter().enumerate() {
                while matched > 0 && pattern[matched] != byte {
                    matched = prefixes[matched - 1];
                }
                if pattern[matched] == byte {
                    matched += 1;
                }
                if matched == pattern.len() {
                    let start = end + 1 - matched;
                    if start < safe_end {
                        best[start] = Some(0);
                    }
                    matched = prefixes[matched - 1];
                }
            }
            return best;
        }
        let mut state = 0;
        for (end, &byte) in input.iter().enumerate() {
            while state != 0 && self.nodes[state].child(byte).is_none() {
                state = self.nodes[state].failure;
            }
            state = self.nodes[state].child(byte).unwrap_or(0);
            let mut output = Some(state);
            while let Some(node) = output {
                for &index in &self.nodes[node].patterns {
                    let start = end + 1 - self.lengths[index];
                    if start < safe_end
                        && best[start]
                            .is_none_or(|previous| self.lengths[index] > self.lengths[previous])
                    {
                        best[start] = Some(index);
                    }
                }
                output = self.nodes[node].output_link;
            }
        }
        best
    }
}

impl OutputMasker {
    fn new(replacements: Vec<(String, String)>) -> Self {
        let mut patterns = Vec::with_capacity(replacements.len());
        let mut references = Vec::with_capacity(replacements.len());
        let mut longest_value = 0;
        for (value, reference) in replacements {
            if value.is_empty() {
                continue;
            }
            longest_value = longest_value.max(value.len());
            patterns.push(value);
            references.push(reference.into_bytes());
        }
        Self {
            matcher: ValueMatcher::new(&patterns),
            references,
            pending: Vec::new(),
            longest_value,
        }
    }

    fn mask(&mut self, chunk: &[u8], finish: bool) -> Vec<u8> {
        if self.longest_value == 0 {
            return chunk.to_vec();
        }
        self.pending.extend_from_slice(chunk);
        let safe_end = if finish {
            self.pending.len()
        } else {
            self.pending
                .len()
                .saturating_sub(self.longest_value.saturating_sub(1))
        };
        if safe_end == 0 {
            return Vec::new();
        }
        if let Some((pattern, _)) = &self.matcher.single {
            let first_byte = pattern[0];
            if !self.pending.contains(&first_byte) {
                let output = self.pending[..safe_end].to_vec();
                self.pending.drain(..safe_end);
                return output;
            }
        }
        let mut output = Vec::new();
        let mut cursor = 0;
        let matches = self.matcher.matches(&self.pending, safe_end);
        while cursor < safe_end {
            let Some(offset) = matches[cursor..].iter().position(Option::is_some) else {
                break;
            };
            let start = cursor + offset;
            let index = matches[start].expect("matched position");
            output.extend_from_slice(&self.pending[cursor..start]);
            output.extend_from_slice(&self.references[index]);
            cursor = start + self.matcher.lengths[index];
        }
        if cursor < safe_end {
            output.extend_from_slice(&self.pending[cursor..safe_end]);
            cursor = safe_end;
        }
        self.pending.drain(..cursor);
        output
    }
}

#[cfg(test)]
mod tests {
    use super::OutputMasker;

    #[test]
    fn masks_overlapping_and_multibyte_values_at_every_chunk_boundary() {
        let replacements = vec![
            ("secret".into(), "{{short}}".into()),
            ("secret-123".into(), "{{long}}".into()),
            ("secret-124".into(), "{{sibling}}".into()),
            ("密钥🙂".into(), "{{unicode}}".into()),
        ];
        let input = "xsecret-123y密钥🙂zsecret-124wsecret".as_bytes();
        let expected = b"x{{long}}y{{unicode}}z{{sibling}}w{{short}}";
        for chunk_size in 1..=input.len() {
            let mut masker = OutputMasker::new(replacements.clone());
            let mut output = Vec::new();
            for chunk in input.chunks(chunk_size) {
                output.extend(masker.mask(chunk, false));
            }
            output.extend(masker.mask(&[], true));
            assert_eq!(output, expected, "chunk size {chunk_size}");
        }
    }

    #[test]
    fn long_partial_prefix_does_not_mask_unmatched_output() {
        let value = format!("{}b", "a".repeat(4095));
        let input = vec![b'a'; 64 * 1024];
        let mut masker = OutputMasker::new(vec![(value, "{{ref}}".into())]);
        let mut output = Vec::new();
        for chunk in input.chunks(8192) {
            output.extend(masker.mask(chunk, false));
        }
        output.extend(masker.mask(&[], true));
        assert_eq!(output, input);
    }

    #[test]
    fn streaming_mask_matches_longest_value_reference() {
        fn next(state: &mut u64) -> usize {
            *state = state.wrapping_mul(6364136223846793005).wrapping_add(1);
            (*state >> 32) as usize
        }

        let alphabet = b"abXY09-";
        for seed in 0..2000 {
            let mut state = seed + 1;
            let mut replacements = Vec::new();
            for index in 0..1 + next(&mut state) % 8 {
                let value: String = (0..1 + next(&mut state) % 10)
                    .map(|_| alphabet[next(&mut state) % alphabet.len()] as char)
                    .collect();
                replacements.push((value, format!("<ref{index}>")));
            }
            let mut input = Vec::new();
            for _ in 0..100 {
                if next(&mut state) % 5 == 0 {
                    let index = next(&mut state) % replacements.len();
                    input.extend_from_slice(replacements[index].0.as_bytes());
                } else {
                    input.push(alphabet[next(&mut state) % alphabet.len()]);
                }
            }
            let mut expected = Vec::new();
            let mut cursor = 0;
            while cursor < input.len() {
                let mut matched: Option<&(String, String)> = None;
                for replacement in &replacements {
                    if input[cursor..].starts_with(replacement.0.as_bytes())
                        && matched.is_none_or(|(value, _)| replacement.0.len() > value.len())
                    {
                        matched = Some(replacement);
                    }
                }
                if let Some((value, reference)) = matched {
                    expected.extend_from_slice(reference.as_bytes());
                    cursor += value.len();
                } else {
                    expected.push(input[cursor]);
                    cursor += 1;
                }
            }
            let mut masker = OutputMasker::new(replacements);
            let mut actual = Vec::new();
            for chunk in input.chunks(1 + next(&mut state) % 17) {
                actual.extend(masker.mask(chunk, false));
            }
            actual.extend(masker.mask(&[], true));
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
