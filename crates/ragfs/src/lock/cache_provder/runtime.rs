use super::*;

impl RedisPathLockProvider {
    /// Return the Redis HASH key responsible for one logical path.
    pub(super) fn tokens_key_for_path(&self, path: &str) -> PathLockResult<String> {
        validate_normalized_logical_path(path)?;
        let suffix = if path == "/" || path == "/local" {
            "global".to_string()
        } else {
            let scope = path
                .strip_prefix("/local/")
                .and_then(|path| path.split('/').next())
                .filter(|scope| !scope.is_empty())
                .ok_or_else(|| {
                    PathLockError::InvalidRequest(format!(
                        "cache PathLock path must be '/' or below '/local': {path}"
                    ))
                })?;
            match scope {
                "_system" => "scope:_system".to_string(),
                account => format!("scope:account:{account}"),
            }
        };
        Ok(format!(
            "{{{}}}:ov:pathlock:{suffix}:tokens",
            self.service_name
        ))
    }

    /// Reject Tree roots whose descendants span multiple Redis HASHes.
    pub(super) fn validate_lock_scope(path: &str, kind: PathLockKind) -> PathLockResult<()> {
        if kind == PathLockKind::Tree && (path == "/" || path == "/local") {
            return Err(PathLockError::InvalidRequest(format!(
                "cache PathLock Tree lock at '{path}' spans multiple scopes; \
                 use '/local/<account>' or '/local/_system' as the Tree root"
            )));
        }
        Ok(())
    }

    /// Return the shared Redis HASH key for one same-scope path batch.
    pub(super) fn tokens_key_for_batch<'a>(
        &self,
        paths: impl IntoIterator<Item = &'a str>,
    ) -> PathLockResult<String> {
        let mut paths = paths.into_iter();
        let first_path = paths.next().ok_or_else(|| {
            PathLockError::InvalidRequest("lock request batch must not be empty".to_string())
        })?;
        let key = self.tokens_key_for_path(first_path)?;
        for path in paths {
            if self.tokens_key_for_path(path)? != key {
                return Err(PathLockError::InvalidRequest(
                    "cache PathLock batch paths must belong to one scope".to_string(),
                ));
            }
        }
        Ok(key)
    }

    /// Execute one registered PathLock script and decode its provider-neutral result.
    pub(super) async fn execute(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<ScriptValue> {
        self.runtime
            .execute_script(ScriptRequest {
                script_id: script_id.to_string(),
                keys: vec![key],
                args,
            })
            .await
            .and_then(|result| result.decode())
            .map_err(Self::cache_error)
    }

    /// Execute one script and require an array result.
    pub(super) async fn execute_array(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<Vec<ScriptValue>> {
        match self.execute(script_id, key, args).await? {
            ScriptValue::Array(values) => Ok(values),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {script_id} returned non-array result: {other:?}"
            ))),
        }
    }

    /// Execute one script and require an integer result.
    pub(super) async fn execute_integer(
        &self,
        script_id: &str,
        key: String,
        args: Vec<Bytes>,
    ) -> PathLockResult<i64> {
        match self.execute(script_id, key, args).await? {
            ScriptValue::Integer(value) => Ok(value),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {script_id} returned non-integer result: {other:?}"
            ))),
        }
    }

    /// Convert one cache runtime error into a PathLock I/O error.
    pub(super) fn cache_error(error: CacheError) -> PathLockError {
        PathLockError::Io(format!("Redis PathLock operation failed: {error}"))
    }

    /// Decode one script value as UTF-8 text.
    pub(super) fn result_string<'a>(value: &'a ScriptValue, operation: &str) -> PathLockResult<&'a str> {
        match value {
            ScriptValue::Bytes(value) => std::str::from_utf8(value).map_err(|error| {
                PathLockError::Internal(format!(
                    "Redis PathLock {operation} returned invalid UTF-8: {error}"
                ))
            }),
            other => Err(PathLockError::Internal(format!(
                "Redis PathLock {operation} returned non-string value: {other:?}"
            ))),
        }
    }

    /// Return the leading status string from one script array.
    pub(super) fn result_status<'a>(values: &'a [ScriptValue], operation: &str) -> PathLockResult<&'a str> {
        values
            .first()
            .ok_or_else(|| {
                PathLockError::Internal(format!(
                    "Redis PathLock {operation} returned an empty result"
                ))
            })
            .and_then(|value| Self::result_string(value, operation))
    }

    /// Decode one raw Redis token and include its path in decoding errors.
    pub(super) fn decode_token(path: &str, raw: &str) -> PathLockResult<LockToken> {
        LockTokenCodec::decode(raw)
            .map_err(|_| PathLockError::InvalidToken(format!("invalid token at '{path}': {raw}")))
    }

    /// Decode the shared single-token and batch per-path refresh response.
    pub(super) fn decode_refresh_result(lock_path: &str, values: &[ScriptValue]) -> PathLockResult<bool> {
        let status = Self::result_status(values, "refresh")?;
        match status {
            "ok" if values.len() == 1 => Ok(true),
            "missing" | "owner_mismatch" if values.len() == 1 => Ok(false),
            "invalid_token" if values.len() == 2 => Err(PathLockError::InvalidToken(format!(
                "invalid token at '{lock_path}': {}",
                Self::result_string(&values[1], "refresh")?
            ))),
            "invalid_request" if values.len() == 2 => Err(PathLockError::InvalidRequest(
                Self::result_string(&values[1], "refresh")?.to_string(),
            )),
            _ => Err(PathLockError::Internal(format!(
                "Redis PathLock refresh returned invalid status '{status}'"
            ))),
        }
    }

    /// Decode a conflict or invalid-token script response.
    pub(super) fn decode_error_result(
        operation: &str,
        values: &[ScriptValue],
    ) -> PathLockResult<PathLockError> {
        let status = Self::result_status(values, operation)?;
        match status {
            "conflict" if values.len() == 3 => {
                let path = Self::result_string(&values[1], operation)?;
                let raw = Self::result_string(&values[2], operation)?;
                let token = Self::decode_token(path, raw)?;
                Ok(PathLockError::Conflict {
                    lock_path: path.to_string(),
                    owner: token.owner_id,
                    kind: token.lock_type,
                })
            }
            "invalid_token" if values.len() == 3 => {
                let path = Self::result_string(&values[1], operation)?;
                let raw = Self::result_string(&values[2], operation)?;
                Ok(PathLockError::InvalidToken(format!(
                    "invalid token at '{path}': {raw}"
                )))
            }
            "invalid_request" if values.len() == 2 => Ok(PathLockError::InvalidRequest(
                Self::result_string(&values[1], operation)?.to_string(),
            )),
            "recovery_required"
                if values.len() == 2
                    && Self::result_string(&values[1], operation)? == "token_unavailable" =>
            {
                Ok(PathLockError::HandoffRecoveryRequired {
                    reason: HandoffRecoveryReason::TokenUnavailable,
                })
            }
            "changed" if values.len() == 2 => Ok(PathLockError::Io(format!(
                "Redis PathLock rollback value changed at '{}'",
                Self::result_string(&values[1], operation)?
            ))),
            _ => Ok(PathLockError::Internal(format!(
                "Redis PathLock {operation} returned invalid status '{status}'"
            ))),
        }
    }

    /// Encode one string-like script argument.
    pub(super) fn bytes(value: impl AsRef<[u8]>) -> Bytes {
        Bytes::copy_from_slice(value.as_ref())
    }

    /// Return the string code used by Redis for one lock kind.
    pub(super) fn kind_code(kind: PathLockKind) -> &'static str {
        match kind {
            PathLockKind::Exact => "E",
            PathLockKind::Tree => "T",
        }
    }

}
