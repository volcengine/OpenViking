#[derive(Clone)]
struct SharedFileWriter {
    file: Arc<Mutex<std::fs::File>>,
}

impl Write for SharedFileWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        let mut file = self
            .file
            .lock()
            .map_err(|_| io::Error::other("shared tracing log file lock poisoned"))?;
        file.write(buf)
    }

    fn flush(&mut self) -> io::Result<()> {
        let mut file = self
            .file
            .lock()
            .map_err(|_| io::Error::other("shared tracing log file lock poisoned"))?;
        file.flush()
    }
}

struct TracingFileState {
    path: PathBuf,
    file: Arc<Mutex<std::fs::File>>,
}

/// Open the configured Rust tracing log file.
fn open_tracing_log_file(path: &Path) -> Result<std::fs::File, String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("failed to create Rust tracing log dir: {e}"))?;
    }
    OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
        .map_err(|e| format!("failed to open Rust tracing log file: {e}"))
}

/// Swap the shared Rust tracing file handle to the current active log file.
fn replace_tracing_log_file(shared: &Arc<Mutex<std::fs::File>>, path: &Path) -> Result<(), String> {
    let new_file = open_tracing_log_file(path)?;
    let mut file = shared
        .lock()
        .map_err(|_| "shared tracing log file lock poisoned".to_string())?;
    *file = new_file;
    Ok(())
}

/// Parse the configured OpenViking log level into a Rust tracing level.
fn parse_tracing_level(log_level: &str) -> Result<Level, String> {
    match log_level.to_ascii_uppercase().as_str() {
        "TRACE" => Ok(Level::TRACE),
        "DEBUG" => Ok(Level::DEBUG),
        "INFO" => Ok(Level::INFO),
        "WARN" | "WARNING" => Ok(Level::WARN),
        "CRITICAL" => Ok(Level::ERROR),
        "ERROR" => Ok(Level::ERROR),
        other => Err(format!("unsupported Rust tracing level: {other}")),
    }
}

/// Build the tracing writer from the configured OpenViking log output target.
fn build_tracing_writer(log_output: &str) -> Result<(BoxMakeWriter, WorkerGuard), String> {
    match log_output {
        "stdout" => {
            let (writer, guard) = tracing_appender::non_blocking(std::io::stdout());
            Ok((BoxMakeWriter::new(writer), guard))
        }
        "stderr" => {
            let (writer, guard) = tracing_appender::non_blocking(std::io::stderr());
            Ok((BoxMakeWriter::new(writer), guard))
        }
        path => {
            let path = Path::new(path);
            let shared = Arc::new(Mutex::new(open_tracing_log_file(path)?));
            let state = TracingFileState {
                path: path.to_path_buf(),
                file: shared.clone(),
            };
            RUST_TRACING_FILE_STATE
                .set(state)
                .map_err(|_| "Rust tracing file state already initialized".to_string())?;
            let (writer, guard) = tracing_appender::non_blocking(SharedFileWriter { file: shared });
            Ok((BoxMakeWriter::new(writer), guard))
        }
    }
}

/// Reopen the Rust tracing file writer after Python log rotation.
fn reopen_tracing_file_impl() -> Result<(), String> {
    let Some(state) = RUST_TRACING_FILE_STATE.get() else {
        return Ok(());
    };
    replace_tracing_log_file(&state.file, &state.path)
}

/// Reopen the Rust tracing log file after Python rotates the active log file.
#[pyfunction]
fn reopen_tracing_file() -> PyResult<()> {
    reopen_tracing_file_impl().map_err(PyRuntimeError::new_err)
}

fn log_timestamp(time: SystemTime) -> String {
    let elapsed = time.duration_since(UNIX_EPOCH).unwrap_or_default();
    let seconds = format_rfc3339(elapsed.as_secs());
    format!(
        "{}.{:03}Z",
        seconds.trim_end_matches('Z'),
        elapsed.subsec_millis()
    )
}

#[derive(Default)]
struct SafeLogFields {
    message: String,
    fields: serde_json::Map<String, serde_json::Value>,
}

impl SafeLogFields {
    fn allowed(name: &str) -> bool {
        matches!(
            name,
            "request_id"
                | "trace_id"
                | "span_id"
                | "account_id"
                | "user_id"
                | "operation"
                | "error_type"
                | "error_code"
                | "task_id"
                | "task_type"
                | "task_status"
                | "task_stage"
                | "total"
                | "failed"
                | "skipped"
                | "duration_ms"
                | "attempt"
                | "retry_limit"
                | "delay"
                | "queue_depth"
                | "degraded"
                | "truncated"
                | "payload"
        )
    }
}

impl Visit for SafeLogFields {
    fn record_debug(&mut self, field: &Field, value: &dyn std::fmt::Debug) {
        if field.name() == "message" {
            self.message = format!("{value:?}");
        }
    }
    fn record_str(&mut self, field: &Field, value: &str) {
        if field.name() == "message" {
            self.message = value.to_string();
        } else if Self::allowed(field.name())
            && value.len() <= 256
            && !value.contains("peers/")
            && !value.contains("://")
            && !value.starts_with('/')
            && value
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || "._:-/{}".contains(ch))
        {
            self.fields.insert(field.name().into(), value.into());
        }
    }
    fn record_u64(&mut self, field: &Field, value: u64) {
        if Self::allowed(field.name()) {
            self.fields.insert(field.name().into(), value.into());
        }
    }
    fn record_i64(&mut self, field: &Field, value: i64) {
        if value >= 0 && Self::allowed(field.name()) {
            self.fields.insert(field.name().into(), value.into());
        }
    }
    fn record_bool(&mut self, field: &Field, value: bool) {
        if Self::allowed(field.name()) {
            self.fields.insert(field.name().into(), value.into());
        }
    }
    fn record_f64(&mut self, field: &Field, value: f64) {
        if value.is_finite() && value >= 0.0 && Self::allowed(field.name()) {
            self.fields.insert(field.name().into(), value.into());
        }
    }
}

struct CommonLogFormat;
impl<S, N> FormatEvent<S, N> for CommonLogFormat
where
    S: tracing::Subscriber + for<'lookup> LookupSpan<'lookup>,
    N: for<'writer> FormatFields<'writer> + 'static,
{
    fn format_event(
        &self,
        _ctx: &FmtContext<'_, S, N>,
        mut writer: Writer<'_>,
        event: &tracing::Event<'_>,
    ) -> std::fmt::Result {
        let timestamp = log_timestamp(SystemTime::now());
        let mut values = SafeLogFields::default();
        event.record(&mut values);
        if values.message.len() > 2048 {
            let original_length = values.message.len();
            let mut end = 2048;
            while !values.message.is_char_boundary(end) {
                end -= 1;
            }
            values.message.truncate(end);
            values.fields.insert("truncated".into(), true.into());
            values
                .fields
                .insert("original_length".into(), original_length.into());
        }
        let level = match *event.metadata().level() {
            Level::TRACE | Level::DEBUG => "DEBUG",
            Level::INFO => "INFO",
            Level::WARN => "WARNING",
            Level::ERROR => "ERROR",
        };
        let request_id = values.fields.remove("request_id");
        let trace_id = values.fields.remove("trace_id");
        write!(
            writer,
            "{} {} {} pid={} request_id={} trace_id={} message={}",
            timestamp,
            level,
            event.metadata().target(),
            std::process::id(),
            request_id.as_ref().and_then(|v| v.as_str()).unwrap_or("-"),
            trace_id.as_ref().and_then(|v| v.as_str()).unwrap_or("-"),
            serde_json::to_string(&values.message).map_err(|_| std::fmt::Error)?
        )?;
        for (name, value) in values.fields {
            write!(writer, " {name}={value}")?;
        }
        writeln!(writer)
    }
}

/// Initialize Rust tracing once for the embedded ragfs binding process.
///
/// Logging policy:
/// https://github.com/volcengine/OpenViking/blob/main/docs/repository/LOGGING.md
fn init_tracing(log_level: &str, log_output: &str) -> PyResult<()> {
    let max_level = parse_tracing_level(log_level).map_err(PyValueError::new_err)?;
    let configuration = (max_level.to_string(), log_output.to_owned());
    let result = RUST_TRACING_INIT_RESULT.get_or_init(|| {
        let (writer, guard) = build_tracing_writer(log_output)?;
        let subscriber = tracing_subscriber::fmt()
            .with_max_level(max_level)
            .with_ansi(false)
            .event_format(CommonLogFormat)
            .with_writer(writer)
            .finish();
        tracing::subscriber::set_global_default(subscriber)
            .map_err(|e| format!("failed to install Rust tracing subscriber: {e}"))?;
        RUST_TRACING_GUARD
            .set(guard)
            .map_err(|_| "failed to retain Rust tracing worker guard".to_string())?;
        let _ = RUST_TRACING_CONFIG.set(configuration.clone());
        Ok(())
    });
    result
        .as_ref()
        .map_err(|err| PyRuntimeError::new_err(err.clone()))?;
    if RUST_TRACING_CONFIG.get() != Some(&configuration) {
        return Err(PyRuntimeError::new_err(
            "Rust logging configuration changes require a process restart",
        ));
    }
    Ok(())
}
