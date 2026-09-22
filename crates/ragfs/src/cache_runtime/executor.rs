//! Synchronous execution bridge for CacheRuntime.

use super::{CacheError, CacheResult};
use std::future::Future;
use std::sync::mpsc;

type Job = Box<dyn FnOnce(&tokio::runtime::Runtime) + Send + 'static>;

pub(crate) struct RuntimeExecutor {
    sender: mpsc::Sender<Job>,
}

impl RuntimeExecutor {
    pub(crate) fn new() -> CacheResult<Self> {
        let (sender, receiver) = mpsc::channel::<Job>();
        let (ready_sender, ready_receiver) = mpsc::sync_channel(1);
        std::thread::Builder::new()
            .name("ragfs-cache-runtime".into())
            .spawn(move || {
                let runtime = tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(1)
                    .enable_all()
                    .build();
                match runtime {
                    Ok(runtime) => {
                        let _ = ready_sender.send(Ok(()));
                        while let Ok(job) = receiver.recv() {
                            job(&runtime);
                        }
                    }
                    Err(error) => {
                        let _ = ready_sender.send(Err(error.to_string()));
                    }
                }
            })
            .map_err(|error| CacheError::Internal(error.to_string()))?;
        ready_receiver
            .recv()
            .map_err(|_| CacheError::Internal("cache runtime executor failed to start".into()))?
            .map_err(CacheError::Internal)?;
        Ok(Self { sender })
    }

    pub(crate) fn run<T, F>(&self, future: F) -> CacheResult<T>
    where
        T: Send + 'static,
        F: Future<Output = CacheResult<T>> + Send + 'static,
    {
        if tokio::runtime::Handle::try_current().is_ok() {
            return Err(CacheError::InvalidExecutionContext);
        }

        let (result_sender, result_receiver) = mpsc::sync_channel(1);
        self.sender
            .send(Box::new(move |runtime| {
                let _ = result_sender.send(runtime.block_on(future));
            }))
            .map_err(|_| CacheError::Closed)?;
        result_receiver
            .recv()
            .map_err(|_| CacheError::Internal("cache runtime executor stopped".into()))?
    }
}
