use super::*;

impl RedisPathLockProvider {
    /// Read and decode multiple token fields while preserving input order.
    async fn read_many_tokens(&self, paths: &[String]) -> PathLockResult<Vec<Option<LockToken>>> {
        if paths.is_empty() {
            return Ok(Vec::new());
        }
        let key = self.tokens_key_for_batch(paths.iter().map(String::as_str))?;
        let values = self
            .execute_array(
                READ_MANY_ID,
                key,
                paths.iter().map(Self::bytes).collect::<Vec<_>>(),
            )
            .await?;
        if values.len() != paths.len() {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock read_many returned {} values for {} paths",
                values.len(),
                paths.len()
            )));
        }
        values
            .iter()
            .zip(paths)
            .map(|(value, path)| match value {
                ScriptValue::Null => Ok(None),
                ScriptValue::Bytes(raw) => {
                    let raw = std::str::from_utf8(raw).map_err(|error| {
                        PathLockError::InvalidToken(format!(
                            "invalid UTF-8 token at '{path}': {error}"
                        ))
                    })?;
                    Self::decode_token(path, raw).map(Some)
                }
                other => Err(PathLockError::Internal(format!(
                    "Redis PathLock read_many returned invalid value for '{path}': {other:?}"
                ))),
            })
            .collect()
    }
}

#[async_trait]
impl PathLockProvider for RedisPathLockProvider {
    async fn replace_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        restored: LockHolder,
        _stale_before: u128,
    ) -> PathLockResult<()> {
        if !LockTokenCodec::valid_id(owner)
            || !LockTokenCodec::valid_id(holder)
            || !LockTokenCodec::valid_id(&restored.id)
            || holder == restored.id
        {
            return Err(PathLockError::InvalidRequest(
                "invalid holder rollback identity".into(),
            ));
        }
        Self::validate_lock_scope(path, restored.kind)?;
        let values = self
            .execute_array(
                REPLACE_HOLDER_ID,
                self.tokens_key_for_path(path)?,
                vec![
                    Self::bytes(path),
                    Self::bytes(owner),
                    Self::bytes(holder),
                    Self::bytes(&restored.id),
                    Self::bytes(Self::kind_code(restored.kind)),
                    Self::bytes(restored.time_ns.to_string()),
                    Self::bytes(self.lease_ms.to_string()),
                ],
            )
            .await?;
        Self::decode_refresh_result(path, &values).map(|_| ())
    }

    async fn mutate_holder(
        &self,
        path: &str,
        owner: &str,
        holder: &str,
        _now: u128,
        _stale_before: u128,
        release: bool,
    ) -> PathLockResult<bool> {
        if !LockTokenCodec::valid_id(owner) || !LockTokenCodec::valid_id(holder) {
            return Err(PathLockError::InvalidRequest(
                "invalid holder identity".into(),
            ));
        }
        let values = self
            .execute_array(
                MUTATE_HOLDER_ID,
                self.tokens_key_for_path(path)?,
                vec![
                    Self::bytes(path),
                    Self::bytes(owner),
                    Self::bytes(holder),
                    Self::bytes(self.lease_ms.to_string()),
                    Self::bytes(if release { "1" } else { "0" }),
                ],
            )
            .await?;
        Self::decode_refresh_result(path, &values)
    }

    async fn refresh_holders(
        &self,
        paths: &[String],
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
    ) -> Vec<PathLockResult<bool>> {
        self.refresh_tokens(paths, &format!("v2|{owner}|{holder}|{stale_before}"), now)
            .await
    }
    async fn try_acquire_holders_atomic(
        &self,
        requests: &[PathLockRequest],
        owner: &str,
        holder: &str,
        now: u128,
        stale_before: u128,
        source: Option<&str>,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        if !LockTokenCodec::valid_id(owner)
            || !LockTokenCodec::valid_id(holder)
            || source.is_some_and(|s| !LockTokenCodec::valid_id(s))
        {
            return Err(PathLockError::InvalidRequest(
                "invalid holder identity".into(),
            ));
        }
        self.try_acquire_batch_atomic_with_stats(
            requests,
            &format!("v2|{owner}|{holder}|{}", source.unwrap_or("")),
            now,
            stale_before,
        )
        .await
    }

    async fn compare_and_remove_token(
        &self,
        path: &str,
        expected: &LockToken,
    ) -> PathLockResult<bool> {
        self.execute_integer(
            COMPARE_REMOVE_ID,
            self.tokens_key_for_path(path)?,
            vec![
                Self::bytes(path),
                Self::bytes(LockTokenCodec::encode(expected)),
                Self::bytes(self.lease_ms.to_string()),
            ],
        )
        .await
        .map(|n| n != 0)
    }
    fn update_metrics(&self, metrics: &mut super::super::metrics::LockMetrics) {
        metrics.redis_acquire = Some(self.acquire_duration.clone());
        metrics.redis_slow_acquisitions = self.slow_acquisitions.load(Ordering::Relaxed);
    }

    /// Return the provider name used by configuration and diagnostics.
    fn name(&self) -> &'static str {
        "cache"
    }

    /// Return logical paths because Redis HASH fields are business paths.
    fn handle_mode(&self) -> PathLockHandleMode {
        PathLockHandleMode::LogicalPath
    }

    /// Atomically check conflicts and acquire one normalized request batch.
    async fn try_acquire_batch_atomic(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        now_ns: u128,
        stale_before_ns: u128,
    ) -> PathLockResult<Option<Vec<AtomicAcquisition>>> {
        Ok(self
            .try_acquire_batch_atomic_with_stats(requests, owner_id, now_ns, stale_before_ns)
            .await?
            .map(|result| result.acquisitions))
    }

    async fn try_acquire_batch_atomic_with_stats(
        &self,
        requests: &[PathLockRequest],
        owner_id: &str,
        _now_ns: u128,
        _stale_before_ns: u128,
    ) -> PathLockResult<Option<AtomicAcquisitionResult>> {
        if requests.is_empty() {
            return Err(PathLockError::InvalidRequest(
                "lock request batch must not be empty".to_string(),
            ));
        }
        for request in requests {
            Self::validate_lock_scope(&request.path, request.kind)?;
        }
        let key =
            self.tokens_key_for_batch(requests.iter().map(|request| request.path.as_str()))?;
        let mut args = vec![
            Self::bytes(owner_id),
            Self::bytes(self.lease_ms.to_string()),
        ];
        for request in requests {
            args.push(Self::bytes(&request.path));
            args.push(Self::bytes(Self::kind_code(request.kind)));
        }
        let started = Instant::now();
        let response = self
            .execute_array(ACQUIRE_BATCH_ID, key.clone(), args)
            .await;
        let elapsed = started.elapsed();
        let result = match &response {
            Ok(values) => match Self::result_status(values, "acquire_batch") {
                Ok("ok") => "ok",
                Ok("conflict") => "conflict",
                Ok("invalid_token") => "invalid_token",
                Ok("invalid_request") => "invalid_request",
                Ok("recovery_required") => "recovery_required",
                _ => "invalid_response",
            },
            Err(_) => "error",
        };
        let includes_tree = requests
            .iter()
            .any(|request| request.kind == PathLockKind::Tree);
        self.acquire_duration.record(
            elapsed,
            &[
                KeyValue::new("kind", if includes_tree { "tree" } else { "exact" }),
                KeyValue::new("result", result),
            ],
        );
        if elapsed > SLOW_ACQUIRE_THRESHOLD {
            self.slow_acquisitions.fetch_add(1, Ordering::Relaxed);
            let scope = key
                .split_once(":ov:pathlock:")
                .and_then(|(_, suffix)| suffix.strip_suffix(":tokens"))
                .unwrap_or(&key);
            tracing::warn!(
                script_id = ACQUIRE_BATCH_ID,
                service_name = %self.service_name,
                scope,
                request_count = requests.len(),
                includes_tree,
                elapsed_ms = elapsed.as_secs_f64() * 1_000.0,
                threshold_ms = SLOW_ACQUIRE_THRESHOLD.as_millis() as u64,
                result,
                error = ?response.as_ref().err(),
                "slow Redis pathlock acquire"
            );
        }
        let values = response?;
        let status = Self::result_status(&values, "acquire_batch")?;
        if status != "ok" {
            return Err(Self::decode_error_result("acquire_batch", &values)?);
        }
        if values.len() < 2 || (values.len() - 2) % 4 != 0 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock acquire_batch returned {} trailing values",
                values.len().saturating_sub(2)
            )));
        }

        let stale_tokens_removed = Self::result_string(&values[1], "acquire_batch")?
            .parse::<usize>()
            .map_err(|_| PathLockError::Internal("invalid Redis stale recovery count".into()))?;
        let mut acquisitions = Vec::with_capacity((values.len() - 2) / 4);
        for values in values[2..].chunks_exact(4) {
            let handle = Self::result_string(&values[0], "acquire_batch")?.to_string();
            let change = Self::result_string(&values[1], "acquire_batch")?;
            let previous = Self::result_string(&values[2], "acquire_batch")?;
            let replacement = Self::result_string(&values[3], "acquire_batch")?;
            let change = match change {
                "created" => AcquisitionChange::Created {
                    replacement: Self::decode_token(&handle, replacement)?,
                },
                "reentrant" => AcquisitionChange::Reentrant,
                "upgraded" => AcquisitionChange::Upgraded {
                    previous: Self::decode_token(&handle, previous)?,
                    replacement: Self::decode_token(&handle, replacement)?,
                },
                other => {
                    return Err(PathLockError::Internal(format!(
                        "Redis PathLock acquire_batch returned unknown change '{other}'"
                    )));
                }
            };
            acquisitions.push(AtomicAcquisition { handle, change });
        }
        Ok(Some(AtomicAcquisitionResult {
            acquisitions,
            stale_tokens_removed,
        }))
    }

    /// Atomically roll back mutations reported by one completed batch.
    async fn rollback_acquisitions_atomic(
        &self,
        acquisitions: &[AtomicAcquisition],
        owner_id: &str,
    ) -> PathLockResult<Option<()>> {
        if acquisitions.is_empty() {
            return Ok(Some(()));
        }
        let key =
            self.tokens_key_for_batch(acquisitions.iter().map(|item| item.handle.as_str()))?;
        let mut args = vec![
            Self::bytes(owner_id),
            Self::bytes(self.lease_ms.to_string()),
        ];
        for acquisition in acquisitions {
            args.push(Self::bytes(&acquisition.handle));
            match &acquisition.change {
                AcquisitionChange::Created { replacement } => {
                    args.push(Self::bytes("created"));
                    args.push(Self::bytes(""));
                    args.push(Self::bytes(LockTokenCodec::encode(replacement)));
                }
                AcquisitionChange::Reentrant => {
                    args.push(Self::bytes("reentrant"));
                    args.push(Self::bytes(""));
                    args.push(Self::bytes(""));
                }
                AcquisitionChange::Upgraded {
                    previous,
                    replacement,
                } => {
                    args.push(Self::bytes("upgraded"));
                    args.push(Self::bytes(LockTokenCodec::encode(previous)));
                    args.push(Self::bytes(LockTokenCodec::encode(replacement)));
                }
            }
        }
        let values = self.execute_array(ROLLBACK_BATCH_ID, key, args).await?;
        let status = Self::result_status(&values, "rollback_batch")?;
        if status == "ok" && values.len() == 1 {
            return Ok(Some(()));
        }
        Err(Self::decode_error_result("rollback_batch", &values)?)
    }

    /// Check whether a logical path is covered by a matching live token.
    async fn is_path_locked_atomic(
        &self,
        path: &str,
        _now_ns: u128,
        _stale_before_ns: u128,
        ignore_stale: bool,
    ) -> PathLockResult<Option<bool>> {
        let key = self.tokens_key_for_path(path)?;
        let values = self
            .execute_array(
                IS_LOCKED_ID,
                key,
                vec![
                    Self::bytes(path),
                    Self::bytes(self.lease_ms.to_string()),
                    Self::bytes(if ignore_stale { "1" } else { "0" }),
                ],
            )
            .await?;
        let status = Self::result_status(&values, "is_locked")?;
        if status != "ok" {
            return Err(Self::decode_error_result("is_locked", &values)?);
        }
        if values.len() != 2 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock is_locked returned {} values",
                values.len()
            )));
        }
        match values[1] {
            ScriptValue::Integer(value) => Ok(Some(value != 0)),
            ref other => Err(PathLockError::Internal(format!(
                "Redis PathLock is_locked returned non-integer value: {other:?}"
            ))),
        }
    }

    /// Read and decode one logical path token.
    async fn read_token(&self, lock_path: &str) -> PathLockResult<Option<LockToken>> {
        self.read_many_tokens(&[lock_path.to_string()])
            .await
            .map(|mut values| values.pop().flatten())
    }

    /// Atomically create one token when the path is absent.
    async fn try_create_token(&self, lock_path: &str, token: &LockToken) -> PathLockResult<()> {
        Self::validate_lock_scope(lock_path, token.lock_type)?;
        let key = self.tokens_key_for_path(lock_path)?;
        let values = self
            .execute_array(
                CREATE_ID,
                key,
                vec![
                    Self::bytes(lock_path),
                    Self::bytes(&token.owner_id),
                    Self::bytes(Self::kind_code(token.lock_type)),
                    Self::bytes(self.lease_ms.to_string()),
                    Self::bytes(LockTokenCodec::encode(token)),
                ],
            )
            .await?;
        let status = Self::result_status(&values, "create")?;
        match status {
            "created" if values.len() == 1 => Ok(()),
            "exists" if values.len() == 2 => {
                let raw = Self::result_string(&values[1], "create")?;
                let current = Self::decode_token(lock_path, raw)?;
                Err(PathLockError::Conflict {
                    lock_path: lock_path.to_string(),
                    owner: current.owner_id,
                    kind: current.lock_type,
                })
            }
            "invalid_request" if values.len() == 2 => Err(PathLockError::InvalidRequest(
                Self::result_string(&values[1], "create")?.to_string(),
            )),
            _ => Err(PathLockError::Internal(format!(
                "Redis PathLock create returned invalid status '{status}'"
            ))),
        }
    }

    /// Replace one complete token snapshot. Same-owner kind-only mutations retain
    /// its time; all new confirmations use Redis TIME, ignoring replacement time.
    /// Historical acquisition restoration uses the separate rollback script.
    async fn compare_and_write_token(
        &self,
        lock_path: &str,
        expected: &LockToken,
        replacement: &LockToken,
    ) -> PathLockResult<bool> {
        Self::validate_lock_scope(lock_path, replacement.lock_type)?;
        let key = self.tokens_key_for_path(lock_path)?;
        self.execute_integer(
            COMPARE_WRITE_ID,
            key,
            vec![
                Self::bytes(lock_path),
                Self::bytes(LockTokenCodec::encode(expected)),
                Self::bytes(&replacement.owner_id),
                Self::bytes(Self::kind_code(replacement.lock_type)),
                Self::bytes(if !replacement.holders.is_empty() {
                    "holders"
                } else if replacement.owner_id == expected.owner_id
                    && replacement.time_ns == expected.time_ns
                {
                    "preserve"
                } else {
                    "confirm"
                }),
                Self::bytes(self.lease_ms.to_string()),
                Self::bytes(LockTokenCodec::encode(replacement)),
            ],
        )
        .await
        .map(|value| value != 0)
    }

    /// Refresh one owned token while preserving its lock kind.
    async fn refresh_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        _time_ns: u128,
    ) -> PathLockResult<bool> {
        let key = self.tokens_key_for_path(lock_path)?;
        let values = self
            .execute_array(
                REFRESH_ID,
                key,
                vec![
                    Self::bytes(lock_path),
                    Self::bytes(owner_id),
                    Self::bytes(self.lease_ms.to_string()),
                ],
            )
            .await?;
        Self::decode_refresh_result(lock_path, &values)
    }

    /// Refresh each scope HASH in bounded batches while preserving input order.
    async fn refresh_tokens(
        &self,
        lock_paths: &[String],
        owner_id: &str,
        _time_ns: u128,
    ) -> Vec<PathLockResult<bool>> {
        let mut results: Vec<_> = lock_paths.iter().map(|_| Ok(false)).collect();
        let mut groups: BTreeMap<String, Vec<(usize, &str)>> = BTreeMap::new();
        for (index, path) in lock_paths.iter().enumerate() {
            match self.tokens_key_for_path(path) {
                Ok(key) => groups.entry(key).or_default().push((index, path)),
                Err(error) => results[index] = Err(error),
            }
        }
        for (key, paths) in groups {
            for batch in paths.chunks(REFRESH_BATCH_SIZE) {
                let mut args = vec![
                    Self::bytes(owner_id),
                    Self::bytes(self.lease_ms.to_string()),
                ];
                args.extend(batch.iter().map(|(_, path)| Self::bytes(*path)));
                match self
                    .execute_array(REFRESH_BATCH_ID, key.clone(), args)
                    .await
                {
                    Ok(values)
                        if matches!(
                            Self::result_status(&values, "refresh_batch"),
                            Ok("invalid_request")
                        ) =>
                    {
                        for (index, path) in batch {
                            results[*index] = Self::decode_refresh_result(path, &values);
                        }
                    }
                    Ok(values) if values.len() == batch.len() => {
                        for ((index, path), value) in batch.iter().zip(values) {
                            results[*index] = match value {
                                ScriptValue::Array(values) => {
                                    Self::decode_refresh_result(path, &values)
                                }
                                other => Err(PathLockError::Internal(format!(
                                    "Redis PathLock batch refresh returned non-array result: {other:?}"
                                ))),
                            };
                        }
                    }
                    Ok(values) => {
                        for (index, _) in batch {
                            results[*index] = Err(PathLockError::Internal(format!(
                                "Redis PathLock batch refresh returned {} results for {} paths",
                                values.len(),
                                batch.len()
                            )));
                        }
                    }
                    Err(error) => {
                        for (index, _) in batch {
                            results[*index] =
                                Err(PathLockError::Io(format!("batch refresh failed: {error}")));
                        }
                    }
                }
            }
        }
        results
    }

    /// Remove one owned token using complete-token compare-and-remove.
    async fn remove_token(
        &self,
        lock_path: &str,
        owner_id: &str,
        _force: bool,
    ) -> PathLockResult<bool> {
        let Some(current) = self.read_token(lock_path).await? else {
            return Ok(false);
        };
        if current.owner_id != owner_id || !current.holders.is_empty() {
            return Ok(false);
        }
        let key = self.tokens_key_for_path(lock_path)?;
        self.execute_integer(
            COMPARE_REMOVE_ID,
            key,
            vec![
                Self::bytes(lock_path),
                Self::bytes(LockTokenCodec::encode(&current)),
                Self::bytes(self.lease_ms.to_string()),
            ],
        )
        .await
        .map(|value| value != 0)
    }

    /// Return logical paths equal to or below one root.
    async fn scan_descendant_locks(&self, root: &str) -> PathLockResult<Vec<String>> {
        let key = self.tokens_key_for_path(root)?;
        let values = self
            .execute_array(SCAN_DESCENDANTS_ID, key, vec![Self::bytes(root)])
            .await?;
        let status = Self::result_status(&values, "scan_descendants")?;
        if status != "ok" {
            return Err(Self::decode_error_result("scan_descendants", &values)?);
        }
        if (values.len() - 1) % 2 != 0 {
            return Err(PathLockError::Internal(format!(
                "Redis PathLock scan_descendants returned {} trailing values",
                values.len() - 1
            )));
        }
        let mut paths = Vec::with_capacity((values.len() - 1) / 2);
        for values in values[1..].chunks_exact(2) {
            let path = Self::result_string(&values[0], "scan_descendants")?;
            let raw = Self::result_string(&values[1], "scan_descendants")?;
            Self::decode_token(path, raw)?;
            paths.push(path.to_string());
        }
        Ok(paths)
    }
}
