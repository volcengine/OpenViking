//! In-process provider used by Runtime tests and smoke validation.

use super::provider::CacheProvider;
use super::{
    CacheError, CacheResult, Expiration, ListDirection, ListInsertPosition, ListInsertRequest,
    ListMoveRequest, SetCondition, SetOptions, SetResult,
};
use async_trait::async_trait;
use bytes::Bytes;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

/// Controllable in-memory provider for tests and smoke validation.
pub struct MemoryMockProvider {
    values: RwLock<HashMap<String, Bytes>>,
    expirations: RwLock<HashMap<String, Instant>>,
    sets: RwLock<HashMap<String, HashSet<Bytes>>>,
    lists: RwLock<HashMap<String, VecDeque<Bytes>>>,
    closed: AtomicBool,
    unavailable: AtomicBool,
    delete_failure: AtomicBool,
    gets: AtomicU64,
    batch_gets: AtomicU64,
    active_gets: AtomicU64,
    max_active_gets: AtomicU64,
    seen_get_keys: Mutex<Vec<String>>,
    seen_batch_get_keys: Mutex<Vec<Vec<String>>>,
    get_delay: Duration,
    set_delay: Duration,
    active_sets: AtomicU64,
    max_active_sets: AtomicU64,
    next_set_failure: Mutex<Option<String>>,
}

struct ActiveSetGuard<'a> {
    active_sets: &'a AtomicU64,
}

impl Drop for ActiveSetGuard<'_> {
    fn drop(&mut self) {
        self.active_sets.fetch_sub(1, Ordering::Relaxed);
    }
}

impl MemoryMockProvider {
    /// Create an empty provider.
    pub fn new() -> Self {
        Self {
            values: RwLock::new(HashMap::new()),
            expirations: RwLock::new(HashMap::new()),
            sets: RwLock::new(HashMap::new()),
            lists: RwLock::new(HashMap::new()),
            closed: AtomicBool::new(false),
            unavailable: AtomicBool::new(false),
            delete_failure: AtomicBool::new(false),
            gets: AtomicU64::new(0),
            batch_gets: AtomicU64::new(0),
            active_gets: AtomicU64::new(0),
            max_active_gets: AtomicU64::new(0),
            seen_get_keys: Mutex::new(Vec::new()),
            seen_batch_get_keys: Mutex::new(Vec::new()),
            get_delay: Duration::ZERO,
            set_delay: Duration::ZERO,
            active_sets: AtomicU64::new(0),
            max_active_sets: AtomicU64::new(0),
            next_set_failure: Mutex::new(None),
        }
    }

    /// Delay individual get calls to exercise inflight and concurrency behavior.
    pub fn with_get_delay(mut self, delay: Duration) -> Self {
        self.get_delay = delay;
        self
    }

    /// Delay individual set calls to exercise bounded write concurrency.
    pub fn with_set_delay(mut self, delay: Duration) -> Self {
        self.set_delay = delay;
        self
    }

    /// Fail the next set whose key contains the provided text.
    pub fn fail_next_set_matching(&self, key_fragment: impl Into<String>) {
        *self.next_set_failure.lock().unwrap() = Some(key_fragment.into());
    }

    /// Make all provider operations fail or recover them again.
    pub fn set_unavailable(&self, unavailable: bool) {
        self.unavailable.store(unavailable, Ordering::Release);
    }

    /// Make delete operations fail or recover them again.
    pub fn set_delete_failure(&self, fail: bool) {
        self.delete_failure.store(fail, Ordering::Release);
    }

    /// Return the current number of stored objects.
    pub async fn len(&self) -> usize {
        self.values.read().await.len()
    }

    /// Return whether the provider currently stores no objects.
    pub async fn is_empty(&self) -> bool {
        self.len().await == 0
    }

    /// Return a snapshot of stored keys.
    pub async fn keys(&self) -> Vec<String> {
        self.values.read().await.keys().cloned().collect()
    }

    /// Seed one set for Set query tests.
    pub async fn insert_set_members(&self, key: &str, members: Vec<Bytes>) {
        self.sets
            .write()
            .await
            .entry(key.to_string())
            .or_default()
            .extend(members);
    }

    /// Reset observed read calls and concurrency counters.
    pub fn reset_observed_reads(&self) {
        self.gets.store(0, Ordering::Relaxed);
        self.batch_gets.store(0, Ordering::Relaxed);
        self.active_gets.store(0, Ordering::Relaxed);
        self.max_active_gets.store(0, Ordering::Relaxed);
        self.seen_get_keys.lock().unwrap().clear();
        self.seen_batch_get_keys.lock().unwrap().clear();
    }

    /// Return the number of batch_get calls since the last reset.
    pub fn batch_get_count(&self) -> u64 {
        self.batch_gets.load(Ordering::Relaxed)
    }

    /// Return all keys observed by get and batch_get calls.
    pub fn observed_read_keys(&self) -> Vec<String> {
        let mut keys = self.seen_get_keys.lock().unwrap().clone();
        keys.extend(
            self.seen_batch_get_keys
                .lock()
                .unwrap()
                .iter()
                .flat_map(|batch| batch.iter().cloned()),
        );
        keys
    }

    /// Return the maximum number of concurrent get calls since the last reset.
    pub fn max_concurrent_gets(&self) -> u64 {
        self.max_active_gets.load(Ordering::Relaxed)
    }

    /// Return the maximum number of concurrent set calls.
    pub fn max_concurrent_sets(&self) -> u64 {
        self.max_active_sets.load(Ordering::Relaxed)
    }

    fn ensure_open(&self) -> CacheResult<()> {
        if self.closed.load(Ordering::Acquire) {
            Err(CacheError::Unavailable(
                "memory provider is closed".to_string(),
            ))
        } else if self.unavailable.load(Ordering::Acquire) {
            Err(CacheError::Unavailable(
                "memory provider is unavailable".to_string(),
            ))
        } else {
            Ok(())
        }
    }

    fn enter_get(&self) {
        let active = self.active_gets.fetch_add(1, Ordering::Relaxed) + 1;
        let mut current = self.max_active_gets.load(Ordering::Relaxed);
        while active > current {
            match self.max_active_gets.compare_exchange_weak(
                current,
                active,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(observed) => current = observed,
            }
        }
    }

    fn enter_set(&self) -> ActiveSetGuard<'_> {
        let active = self.active_sets.fetch_add(1, Ordering::Relaxed) + 1;
        let mut current = self.max_active_sets.load(Ordering::Relaxed);
        while active > current {
            match self.max_active_sets.compare_exchange_weak(
                current,
                active,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(observed) => current = observed,
            }
        }
        ActiveSetGuard {
            active_sets: &self.active_sets,
        }
    }

    fn should_fail_set(&self, key: &str) -> bool {
        let mut failure = self.next_set_failure.lock().unwrap();
        if failure
            .as_deref()
            .is_some_and(|key_fragment| key.contains(key_fragment))
        {
            *failure = None;
            true
        } else {
            false
        }
    }
}

impl Default for MemoryMockProvider {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl CacheProvider for MemoryMockProvider {
    async fn get(&self, key: &str) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        self.gets.fetch_add(1, Ordering::Relaxed);
        self.seen_get_keys.lock().unwrap().push(key.to_string());
        self.enter_get();
        if !self.get_delay.is_zero() {
            tokio::time::sleep(self.get_delay).await;
        }
        let mut values = self.values.write().await;
        let mut expirations = self.expirations.write().await;
        purge_expired_value(&mut values, &mut expirations, key);
        let value = values.get(key).cloned();
        self.active_gets.fetch_sub(1, Ordering::Relaxed);
        Ok(value)
    }

    async fn set(&self, key: &str, value: Bytes, options: SetOptions) -> CacheResult<SetResult> {
        self.ensure_open()?;
        if options.keep_ttl && options.expiration.is_some() {
            return Err(CacheError::InvalidArgument(
                "SET cannot combine expiration with keep_ttl".into(),
            ));
        }
        let _active_set = self.enter_set();
        if !self.set_delay.is_zero() {
            tokio::time::sleep(self.set_delay).await;
        }
        if self.should_fail_set(key) {
            return Err(CacheError::Unavailable(format!(
                "memory provider set intentionally failed for {key}"
            )));
        }
        let deadline = options
            .expiration
            .map(|expiration| match expiration {
                Expiration::After(duration) => expiration_deadline(duration),
            })
            .transpose()?;
        let mut values = self.values.write().await;
        let mut expirations = self.expirations.write().await;
        purge_expired_value(&mut values, &mut expirations, key);
        let exists = values.contains_key(key);
        let applies = match options.condition {
            SetCondition::None => true,
            SetCondition::Nx => !exists,
            SetCondition::Xx => exists,
        };
        if !applies {
            return Ok(SetResult::ConditionNotMet);
        }
        self.sets.write().await.remove(key);
        self.lists.write().await.remove(key);
        values.insert(key.to_string(), value);
        if !options.keep_ttl {
            if let Some(deadline) = deadline {
                expirations.insert(key.to_string(), deadline);
            } else {
                expirations.remove(key);
            }
        }
        Ok(SetResult::Applied)
    }

    async fn del(&self, keys: &[String]) -> CacheResult<u64> {
        self.ensure_open()?;
        if self.delete_failure.load(Ordering::Acquire) {
            return Err(CacheError::Unavailable(
                "memory provider delete intentionally failed".to_string(),
            ));
        }
        let mut values = self.values.write().await;
        let mut expirations = self.expirations.write().await;
        let mut sets = self.sets.write().await;
        let mut lists = self.lists.write().await;
        let mut removed = 0;
        for key in keys {
            let existed = values.remove(key).is_some()
                | sets.remove(key).is_some()
                | lists.remove(key).is_some();
            expirations.remove(key);
            if existed {
                removed += 1;
            }
        }
        Ok(removed)
    }

    async fn mget(&self, keys: &[String]) -> CacheResult<Vec<Option<Bytes>>> {
        self.ensure_open()?;
        self.batch_gets.fetch_add(1, Ordering::Relaxed);
        self.seen_batch_get_keys.lock().unwrap().push(keys.to_vec());
        let mut values = self.values.write().await;
        let mut expirations = self.expirations.write().await;
        for key in keys {
            purge_expired_value(&mut values, &mut expirations, key);
        }
        Ok(keys.iter().map(|key| values.get(key).cloned()).collect())
    }

    async fn mset(&self, entries: Vec<(String, Bytes)>) -> CacheResult<()> {
        self.ensure_open()?;
        let keys = entries.iter().map(|(key, _)| key).collect::<Vec<_>>();
        let mut values = self.values.write().await;
        let mut expirations = self.expirations.write().await;
        let mut sets = self.sets.write().await;
        let mut lists = self.lists.write().await;
        for key in keys {
            expirations.remove(key);
            sets.remove(key);
            lists.remove(key);
        }
        values.extend(entries);
        Ok(())
    }

    async fn incr_by(&self, key: &str, delta: i64) -> CacheResult<i64> {
        self.ensure_open()?;
        let mut values = self.values.write().await;
        let current = match values.get(key) {
            None => 0,
            Some(value) => std::str::from_utf8(value)
                .ok()
                .and_then(|value| value.parse::<i64>().ok())
                .ok_or_else(|| {
                    CacheError::InvalidData(format!("value at {key} is not an integer"))
                })?,
        };
        let next = current.checked_add(delta).ok_or_else(|| {
            CacheError::InvalidData(format!("integer operation at {key} overflowed"))
        })?;
        values.insert(key.to_string(), Bytes::from(next.to_string()));
        Ok(next)
    }

    async fn sismember(&self, key: &str, member: &[u8]) -> CacheResult<bool> {
        self.ensure_open()?;
        Ok(self
            .sets
            .read()
            .await
            .get(key)
            .is_some_and(|members| members.contains(member)))
    }

    async fn smembers(&self, key: &str) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        Ok(self
            .sets
            .read()
            .await
            .get(key)
            .map(|members| members.iter().cloned().collect())
            .unwrap_or_default())
    }

    async fn scard(&self, key: &str) -> CacheResult<u64> {
        self.ensure_open()?;
        Ok(self
            .sets
            .read()
            .await
            .get(key)
            .map_or(0, |members| members.len() as u64))
    }

    async fn lpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let list = lists.entry(key.to_string()).or_default();
        for value in values {
            list.push_front(value);
        }
        Ok(list.len() as u64)
    }

    async fn rpush(&self, key: &str, values: Vec<Bytes>) -> CacheResult<u64> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let list = lists.entry(key.to_string()).or_default();
        list.extend(values);
        Ok(list.len() as u64)
    }

    async fn lpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let Some(list) = lists.get_mut(key) else {
            return Ok(Vec::new());
        };
        let count = count.unwrap_or(1) as usize;
        let mut values = Vec::with_capacity(count.min(list.len()));
        for _ in 0..count {
            let Some(value) = list.pop_front() else {
                break;
            };
            values.push(value);
        }
        if list.is_empty() {
            lists.remove(key);
        }
        Ok(values)
    }

    async fn rpop(&self, key: &str, count: Option<u64>) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let Some(list) = lists.get_mut(key) else {
            return Ok(Vec::new());
        };
        let count = count.unwrap_or(1) as usize;
        let mut values = Vec::with_capacity(count.min(list.len()));
        for _ in 0..count {
            let Some(value) = list.pop_back() else {
                break;
            };
            values.push(value);
        }
        if list.is_empty() {
            lists.remove(key);
        }
        Ok(values)
    }

    async fn llen(&self, key: &str) -> CacheResult<u64> {
        self.ensure_open()?;
        Ok(self
            .lists
            .read()
            .await
            .get(key)
            .map_or(0, |list| list.len() as u64))
    }

    async fn lrange(&self, key: &str, start: i64, stop: i64) -> CacheResult<Vec<Bytes>> {
        self.ensure_open()?;
        let lists = self.lists.read().await;
        let Some(list) = lists.get(key) else {
            return Ok(Vec::new());
        };
        let len = list.len() as i64;
        let start = normalize_list_index(len, start).max(0);
        let stop = normalize_list_index(len, stop).min(len - 1);
        if len == 0 || start >= len || start > stop {
            return Ok(Vec::new());
        }
        Ok(list
            .iter()
            .skip(start as usize)
            .take((stop - start + 1) as usize)
            .cloned()
            .collect())
    }

    async fn lindex(&self, key: &str, index: i64) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        let lists = self.lists.read().await;
        let Some(list) = lists.get(key) else {
            return Ok(None);
        };
        let index = normalize_list_index(list.len() as i64, index);
        if index < 0 {
            return Ok(None);
        }
        Ok(list.get(index as usize).cloned())
    }

    async fn lset(&self, key: &str, index: i64, value: Bytes) -> CacheResult<()> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let list = lists
            .get_mut(key)
            .ok_or_else(|| CacheError::InvalidArgument(format!("list {key} does not exist")))?;
        let index = normalize_list_index(list.len() as i64, index);
        let item = usize::try_from(index)
            .ok()
            .and_then(|index| list.get_mut(index))
            .ok_or_else(|| CacheError::InvalidArgument("list index is out of range".into()))?;
        *item = value;
        Ok(())
    }

    async fn ltrim(&self, key: &str, start: i64, stop: i64) -> CacheResult<()> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let Some(list) = lists.get(key) else {
            return Ok(());
        };
        let retained = list_range(list, start, stop);
        if retained.is_empty() {
            lists.remove(key);
        } else {
            lists.insert(key.to_string(), retained.into());
        }
        Ok(())
    }

    async fn lrem(&self, key: &str, count: i64, value: Bytes) -> CacheResult<u64> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let Some(list) = lists.get_mut(key) else {
            return Ok(0);
        };
        let limit = count.unsigned_abs() as usize;
        let mut removed = 0_u64;
        if count >= 0 {
            let mut retained = VecDeque::with_capacity(list.len());
            while let Some(item) = list.pop_front() {
                if item == value && (count == 0 || removed < limit as u64) {
                    removed += 1;
                } else {
                    retained.push_back(item);
                }
            }
            *list = retained;
        } else {
            let mut retained = VecDeque::with_capacity(list.len());
            while let Some(item) = list.pop_back() {
                if item == value && removed < limit as u64 {
                    removed += 1;
                } else {
                    retained.push_front(item);
                }
            }
            *list = retained;
        }
        if list.is_empty() {
            lists.remove(key);
        }
        Ok(removed)
    }

    async fn linsert(&self, request: ListInsertRequest) -> CacheResult<i64> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let Some(list) = lists.get_mut(&request.key) else {
            return Ok(0);
        };
        let Some(pivot) = list.iter().position(|item| item == &request.pivot) else {
            return Ok(-1);
        };
        let index = match request.position {
            ListInsertPosition::Before => pivot,
            ListInsertPosition::After => pivot + 1,
        };
        list.insert(index, request.value);
        Ok(list.len() as i64)
    }

    async fn lmove(&self, request: ListMoveRequest) -> CacheResult<Option<Bytes>> {
        self.ensure_open()?;
        let mut lists = self.lists.write().await;
        let value = {
            let Some(source) = lists.get_mut(&request.source) else {
                return Ok(None);
            };
            match request.source_direction {
                ListDirection::Left => source.pop_front(),
                ListDirection::Right => source.pop_back(),
            }
        };
        let Some(value) = value else {
            return Ok(None);
        };
        let destination = lists.entry(request.destination).or_default();
        match request.destination_direction {
            ListDirection::Left => destination.push_front(value.clone()),
            ListDirection::Right => destination.push_back(value.clone()),
        }
        if lists
            .get(&request.source)
            .is_some_and(|source| source.is_empty())
        {
            lists.remove(&request.source);
        }
        Ok(Some(value))
    }

    async fn close(&self) -> CacheResult<()> {
        self.closed.store(true, Ordering::Release);
        self.values.write().await.clear();
        self.expirations.write().await.clear();
        self.sets.write().await.clear();
        self.lists.write().await.clear();
        Ok(())
    }
}

fn expiration_deadline(duration: Duration) -> CacheResult<Instant> {
    if duration.is_zero() {
        return Err(CacheError::InvalidArgument(
            "expiration must be greater than zero".into(),
        ));
    }
    Instant::now()
        .checked_add(duration)
        .ok_or_else(|| CacheError::InvalidArgument("expiration is too large".into()))
}

fn purge_expired_value(
    values: &mut HashMap<String, Bytes>,
    expirations: &mut HashMap<String, Instant>,
    key: &str,
) {
    if expirations
        .get(key)
        .is_some_and(|deadline| *deadline <= Instant::now())
    {
        expirations.remove(key);
        values.remove(key);
    }
}

fn normalize_list_index(len: i64, index: i64) -> i64 {
    if index < 0 {
        len.saturating_add(index)
    } else {
        index
    }
}

fn list_range(list: &VecDeque<Bytes>, start: i64, stop: i64) -> Vec<Bytes> {
    let len = list.len() as i64;
    if len == 0 {
        return Vec::new();
    }
    let start = normalize_list_index(len, start).max(0);
    let stop = normalize_list_index(len, stop).min(len - 1);
    if start >= len || start > stop {
        return Vec::new();
    }
    list.iter()
        .skip(start as usize)
        .take((stop - start + 1) as usize)
        .cloned()
        .collect()
}
