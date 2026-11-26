use super::GitService;
use rand::Rng;
use std::time::Duration;
use tokio::time::Instant;

const MIN_GC_INTERVAL: Duration = Duration::from_secs(3600);

impl GitService {
    pub(super) fn schedule_gc(&self, account: &str) {
        if !self.gc_enabled {
            return;
        }
        // One extra second accounts for the second-resolution tombstones
        // and strict cutoff even when the random jitter is zero.
        let delay = self
            .gc_grace
            .saturating_add(Duration::from_secs(rand::thread_rng().gen_range(1..=60)));
        let Some(deadline) = Instant::now().checked_add(delay) else {
            tracing::warn!(target:"ragfs::git::gc", %account,"GC deadline exceeds clock range");
            return;
        };
        {
            let mut pending = self.scheduled_gc.lock().unwrap();
            if let Some(last) = pending.get_mut(account) {
                *last = (*last).max(deadline);
                return;
            }
            pending.insert(account.to_owned(), deadline);
        }
        let svc = self.clone();
        let account = account.to_owned();
        tokio::spawn(async move {
            let mut next = deadline;
            loop {
                tokio::time::sleep_until(next).await;
                let started = Instant::now();
                match svc.gc(&account).await {
                    Ok(report) => {
                        tracing::info!(target:"ragfs::git::gc", %account, ?report,"snapshot GC completed")
                    }
                    Err(error) => {
                        tracing::warn!(target:"ragfs::git::gc",%account,%error,"snapshot GC failed")
                    }
                }
                // Keep this worker registered during cooldown so even a new delete
                // arriving after the scan cannot start another worker early.
                let cooldown_end = Instant::now() + MIN_GC_INTERVAL;
                tokio::time::sleep_until(cooldown_end).await;
                let mut pending = svc.scheduled_gc.lock().unwrap();
                // A later delete may still be in its grace period. Run again
                // at its deadline even if it arrived during this collection.
                if let Some(last) = pending.get(&account).filter(|d| **d > started) {
                    next = (*last).max(cooldown_end);
                } else {
                    pending.remove(&account);
                    return;
                }
            }
        });
    }
}
