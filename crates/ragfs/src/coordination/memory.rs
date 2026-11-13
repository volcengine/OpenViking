//! Default process-wide coordination, with no filesystem or network dependency.

use super::cache::{ALLOCATE, LATEST, PUBLISH};
use super::{CLEANUP_INTERVAL, REVISION_TTL};
use crate::cache_runtime::{CacheError, CacheResult};
use std::collections::HashMap;
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

#[derive(Default)]
struct State {
    leaders: HashMap<(String, String), (String, Instant)>,
    counters: HashMap<String, i64>,
    revisions: HashMap<(String, String), (i64, Instant)>,
    last_cleanup: Option<Instant>,
}

impl State {
    fn cleanup(&mut self, now: Instant) {
        if self
            .last_cleanup
            .is_some_and(|last| now.duration_since(last) < CLEANUP_INTERVAL)
        {
            return;
        }
        self.leaders.retain(|_, (_, expires)| *expires > now);
        self.revisions.retain(|_, (_, expires)| *expires > now);
        self.last_cleanup = Some(now);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use uuid::Uuid;

    #[test]
    fn publications_expire_without_resetting_the_service_counter() {
        let backend = MemoryBackend::new(&Uuid::new_v4().to_string());
        assert_eq!(backend.revision("work", ALLOCATE, 0).unwrap(), 1);
        assert_eq!(backend.revision("other", ALLOCATE, 0).unwrap(), 2);
        assert_eq!(backend.revision("work", ALLOCATE, 0).unwrap(), 3);
        backend
            .with_state(|state| {
                assert!(!state
                    .revisions
                    .keys()
                    .any(|(ns, _)| ns == &backend.namespace));
                Ok(())
            })
            .unwrap();
        assert_eq!(backend.revision("work", PUBLISH, 3).unwrap(), 3);
        let key = (backend.namespace.clone(), "work".into());
        let shortened_expiry = Instant::now() + Duration::from_secs(60);
        backend
            .with_state(|state| {
                state.revisions.get_mut(&key).unwrap().1 = shortened_expiry;
                Ok(())
            })
            .unwrap();
        assert_eq!(backend.revision("work", LATEST, 0).unwrap(), 3);
        backend
            .with_state(|state| {
                assert_eq!(state.revisions[&key].1, shortened_expiry);
                Ok(())
            })
            .unwrap();
        assert_eq!(backend.revision("work", PUBLISH, 1).unwrap(), 3);
        backend
            .with_state(|state| {
                assert!(state.revisions[&key].1 > shortened_expiry);
                state.revisions.get_mut(&key).unwrap().1 = Instant::now();
                Ok(())
            })
            .unwrap();
        assert_eq!(backend.revision("work", LATEST, 0).unwrap(), 0);
        assert_eq!(backend.revision("work", ALLOCATE, 0).unwrap(), 4);
    }

    #[test]
    fn cleanup_removes_expired_records_but_keeps_live_records_and_counters() {
        let now = Instant::now();
        let mut state = State::default();
        state.counters.insert("service".into(), 42);
        for (key, expires) in [("expired", now), ("live", now + REVISION_TTL)] {
            state
                .leaders
                .insert(("service".into(), key.into()), ("token".into(), expires));
            state
                .revisions
                .insert(("service".into(), key.into()), (42, expires));
        }
        state.cleanup(now);
        assert_eq!(state.leaders.len(), 1);
        assert_eq!(state.revisions.len(), 1);
        assert!(state
            .leaders
            .contains_key(&("service".into(), "live".into())));
        assert!(state
            .revisions
            .contains_key(&("service".into(), "live".into())));
        assert_eq!(state.counters["service"], 42);
    }

    #[test]
    fn release_deletes_owned_lease() {
        let backend = MemoryBackend::new(&Uuid::new_v4().to_string());
        backend
            .acquire("watch", "owner", Duration::from_secs(60))
            .unwrap();
        backend.release("watch", "owner").unwrap();
        backend
            .with_state(|state| {
                assert!(!state
                    .leaders
                    .contains_key(&(backend.namespace.clone(), "watch".into())));
                Ok(())
            })
            .unwrap();
    }
}

static STATE: OnceLock<Mutex<State>> = OnceLock::new();

pub(super) struct MemoryBackend {
    namespace: String,
}

impl MemoryBackend {
    pub fn new(namespace: &str) -> Self {
        Self {
            namespace: namespace.into(),
        }
    }

    fn with_state<T>(
        &self,
        operation: impl FnOnce(&mut State) -> CacheResult<T>,
    ) -> CacheResult<T> {
        let mut state = STATE
            .get_or_init(Mutex::default)
            .lock()
            .map_err(|_| CacheError::Internal("coordination memory lock poisoned".into()))?;
        state.cleanup(Instant::now());
        operation(&mut state)
    }

    pub fn acquire(&self, key: &str, token: &str, ttl: Duration) -> CacheResult<bool> {
        self.with_state(|state| {
            let key = (self.namespace.clone(), key.to_string());
            let now = Instant::now();
            if state
                .leaders
                .get(&key)
                .is_some_and(|(_, expires)| *expires > now)
            {
                return Ok(false);
            }
            let expires = now
                .checked_add(ttl)
                .ok_or_else(|| CacheError::InvalidArgument("lease expiry overflow".into()))?;
            state.leaders.insert(key, (token.into(), expires));
            Ok(true)
        })
    }

    pub fn renew(&self, key: &str, token: &str, ttl: Duration) -> CacheResult<bool> {
        self.with_state(|state| {
            let key = (self.namespace.clone(), key.to_string());
            let now = Instant::now();
            let Some((owner, expires)) = state.leaders.get_mut(&key) else {
                return Ok(false);
            };
            if *expires <= now {
                state.leaders.remove(&key);
                return Ok(false);
            }
            if owner != token {
                return Ok(false);
            }
            *expires = now
                .checked_add(ttl)
                .ok_or_else(|| CacheError::InvalidArgument("lease expiry overflow".into()))?;
            Ok(true)
        })
    }

    pub fn release(&self, key: &str, token: &str) -> CacheResult<()> {
        self.with_state(|state| {
            let key = (self.namespace.clone(), key.to_string());
            if state
                .leaders
                .get(&key)
                .is_some_and(|(owner, _)| owner == token)
            {
                state.leaders.remove(&key);
            }
            Ok(())
        })
    }

    pub fn revision(&self, key: &str, operation: &str, value: i64) -> CacheResult<i64> {
        self.with_state(|state| {
            let key = (self.namespace.clone(), key.to_string());
            let now = Instant::now();
            match operation {
                ALLOCATE => {
                    let allocated = state.counters.entry(self.namespace.clone()).or_default();
                    *allocated = allocated
                        .checked_add(1)
                        .ok_or_else(|| CacheError::InvalidArgument("revision exhausted".into()))?;
                    Ok(*allocated)
                }
                PUBLISH => {
                    if value > state.counters.get(&self.namespace).copied().unwrap_or(0) {
                        return Err(CacheError::InvalidArgument(
                            "revision was not allocated".into(),
                        ));
                    }
                    let latest = state
                        .revisions
                        .get(&key)
                        .filter(|(_, expires)| *expires > now)
                        .map(|(published, _)| *published)
                        .unwrap_or(0)
                        .max(value);
                    state.revisions.insert(key, (latest, now + REVISION_TTL));
                    Ok(latest)
                }
                LATEST => {
                    if let Some((published, expires)) = state.revisions.get(&key) {
                        if *expires > now {
                            return Ok(*published);
                        }
                        state.revisions.remove(&key);
                    }
                    Ok(0)
                }
                _ => unreachable!(),
            }
        })
    }
}
