//! PyO3 handles for one configured cache runtime.

use bytes::Bytes;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use ragfs::cache_runtime::{
    CacheError, CacheResult, CacheRuntime, Expiration, ListDirection, ListInsertPosition,
    ListInsertRequest, ListMoveRequest, ScriptRequest, ScriptValue, SetCondition, SetOptions,
    SetResult,
};
use std::collections::HashMap;
use std::future::Future;
use std::sync::Arc;
use std::time::Duration;

/// Convert one cache failure to the Python exception required by the binding API.
fn cache_error_to_py(instance: &str, error: CacheError) -> PyErr {
    match error {
        CacheError::InvalidArgument(message) => PyValueError::new_err(message),
        error => PyRuntimeError::new_err(format!("cache instance '{instance}': {error}")),
    }
}

/// Parse a strictly positive duration expressed as fractional seconds.
fn positive_duration(seconds: f64, name: &str) -> PyResult<Duration> {
    if seconds.is_finite() && seconds > 0.0 {
        Duration::try_from_secs_f64(seconds).map_err(|_| {
            PyValueError::new_err(format!("{name} exceeds the supported duration range"))
        })
    } else {
        Err(PyValueError::new_err(format!(
            "{name} must be a finite positive number"
        )))
    }
}

/// Build Redis SET options from the public Python keyword arguments.
fn set_options(
    ttl_seconds: Option<f64>,
    if_not_exists: bool,
    if_exists: bool,
    keep_ttl: bool,
) -> PyResult<SetOptions> {
    if if_not_exists && if_exists {
        return Err(PyValueError::new_err(
            "if_not_exists and if_exists are mutually exclusive",
        ));
    }
    if ttl_seconds.is_some() && keep_ttl {
        return Err(PyValueError::new_err(
            "ttl_seconds cannot be combined with keep_ttl",
        ));
    }
    Ok(SetOptions {
        expiration: ttl_seconds
            .map(|seconds| positive_duration(seconds, "ttl_seconds").map(Expiration::After))
            .transpose()?,
        condition: if if_not_exists {
            SetCondition::Nx
        } else if if_exists {
            SetCondition::Xx
        } else {
            SetCondition::None
        },
        keep_ttl,
    })
}

/// Parse an optional non-negative list pop count.
fn optional_count(count: Option<i64>) -> PyResult<Option<u64>> {
    count
        .map(|count| {
            u64::try_from(count)
                .map_err(|_| PyValueError::new_err("count must be non-negative"))
        })
        .transpose()
}

/// Parse a positive sorted-set result limit.
fn score_limit(limit: i64) -> PyResult<u32> {
    u32::try_from(limit)
        .ok()
        .filter(|limit| *limit > 0)
        .ok_or_else(|| PyValueError::new_err("limit must be a positive 32-bit integer"))
}

/// Parse the documented LINSERT position.
fn list_insert_position(position: &str) -> PyResult<ListInsertPosition> {
    match position {
        "before" => Ok(ListInsertPosition::Before),
        "after" => Ok(ListInsertPosition::After),
        _ => Err(PyValueError::new_err(
            "position must be 'before' or 'after'",
        )),
    }
}

/// Parse the documented LMOVE direction.
fn list_direction(direction: &str, name: &str) -> PyResult<ListDirection> {
    match direction {
        "left" => Ok(ListDirection::Left),
        "right" => Ok(ListDirection::Right),
        _ => Err(PyValueError::new_err(format!(
            "{name} must be 'left' or 'right'"
        ))),
    }
}

/// Convert a provider-neutral script result into its recursive Python representation.
fn script_value_to_py(py: Python<'_>, value: ScriptValue) -> PyResult<Py<PyAny>> {
    match value {
        ScriptValue::Null => Ok(py.None()),
        ScriptValue::Integer(value) => Ok(value.into_pyobject(py)?.into_any().unbind()),
        ScriptValue::Bytes(value) => Ok(PyBytes::new(py, &value).into_any().unbind()),
        ScriptValue::Boolean(value) => {
            let bound = value.into_pyobject(py)?;
            Ok(bound.as_any().clone().unbind())
        }
        ScriptValue::Array(values) => {
            let list = PyList::empty(py);
            for value in values {
                list.append(script_value_to_py(py, value)?)?;
            }
            Ok(list.into_any().unbind())
        }
    }
}

/// Asynchronous Python handle for one configured cache Runtime.
#[pyclass(name = "CacheRuntimeHandle", module = "ragfs_python")]
pub(crate) struct PyCacheRuntimeHandle {
    instance: String,
    runtime: Arc<CacheRuntime>,
}

impl PyCacheRuntimeHandle {
    /// Construct a handle over an already configured Runtime.
    pub(crate) fn new(instance: String, runtime: Arc<CacheRuntime>) -> Self {
        Self { instance, runtime }
    }

    /// Convert a cache future into a Python awaitable with instance-scoped errors.
    fn future<'py, F, T>(&self, py: Python<'py>, future: F) -> PyResult<Bound<'py, PyAny>>
    where
        F: Future<Output = CacheResult<T>> + Send + 'static,
        T: Send + 'static + for<'a> IntoPyObject<'a>,
    {
        let instance = self.instance.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            future
                .await
                .map_err(|error| cache_error_to_py(&instance, error))
        })
    }
}

#[pymethods]
impl PyCacheRuntimeHandle {
    /// Return the configured cache instance name.
    #[getter]
    fn instance(&self) -> String {
        self.instance.clone()
    }

    /// Return the Runtime default TTL in seconds, or None when disabled.
    #[getter]
    fn default_ttl_seconds(&self) -> Option<f64> {
        self.runtime.default_ttl().map(|duration| duration.as_secs_f64())
    }

    /// Read key and return its bytes value or None.
    fn get<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.get(&key).await.map(|value| value.map(|v| v.to_vec())) })
    }

    /// Store bytes at key and return whether the condition allowed the write.
    #[pyo3(signature = (key, value, *, ttl_seconds=None, if_not_exists=false, if_exists=false, keep_ttl=false))]
    fn set<'py>(
        &self,
        py: Python<'py>,
        key: String,
        value: Vec<u8>,
        ttl_seconds: Option<f64>,
        if_not_exists: bool,
        if_exists: bool,
        keep_ttl: bool,
    ) -> PyResult<Bound<'py, PyAny>> {
        let options = set_options(ttl_seconds, if_not_exists, if_exists, keep_ttl)?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .set(&key, Bytes::from(value), options)
                .await
                .map(|result| matches!(result, SetResult::Applied))
        })
    }

    /// Delete keys and return the number removed.
    fn delete<'py>(
        &self,
        py: Python<'py>,
        keys: Vec<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.del(&keys).await })
    }

    /// Unlink keys and return the number removed.
    fn unlink<'py>(
        &self,
        py: Python<'py>,
        keys: Vec<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.unlink(&keys).await })
    }

    /// Read keys and preserve the input order.
    fn mget<'py>(
        &self,
        py: Python<'py>,
        keys: Vec<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .mget(&keys)
                .await
                .map(|values| values.into_iter().map(|value| value.map(|v| v.to_vec())).collect::<Vec<_>>())
        })
    }

    /// Store multiple byte values.
    fn mset<'py>(
        &self,
        py: Python<'py>,
        entries: HashMap<String, Vec<u8>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        let entries = entries
            .into_iter()
            .map(|(key, value)| (key, Bytes::from(value)))
            .collect();
        self.future(py, async move { runtime.mset(entries).await })
    }

    /// Increment key by one and return the resulting value.
    fn incr<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.incr(&key).await })
    }

    /// Decrement key by one and return the resulting value.
    fn decr<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.decr(&key).await })
    }

    /// Increment key by delta and return the resulting value.
    #[pyo3(signature = (key, delta=1))]
    fn incr_by<'py>(
        &self,
        py: Python<'py>,
        key: String,
        delta: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.incr_by(&key, delta).await })
    }

    /// Decrement key by delta and return the resulting value.
    #[pyo3(signature = (key, delta=1))]
    fn decr_by<'py>(
        &self,
        py: Python<'py>,
        key: String,
        delta: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.decr_by(&key, delta).await })
    }

    /// Set key expiration and return whether the key exists.
    fn expire<'py>(
        &self,
        py: Python<'py>,
        key: String,
        ttl_seconds: f64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let duration = positive_duration(ttl_seconds, "ttl_seconds")?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.expire(&key, duration).await })
    }

    /// Read a hash field and return bytes or None.
    fn hget<'py>(
        &self,
        py: Python<'py>,
        key: String,
        field: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.hget(&key, &field).await.map(|value| value.map(|v| v.to_vec())) })
    }

    /// Store a hash field and return whether it was newly added.
    fn hset<'py>(
        &self,
        py: Python<'py>,
        key: String,
        field: String,
        value: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.hset(&key, &field, Bytes::from(value)).await })
    }

    /// Return whether member belongs to the set at key.
    fn sismember<'py>(
        &self,
        py: Python<'py>,
        key: String,
        member: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.sismember(&key, &member).await })
    }

    /// Return all members in the set at key.
    fn smembers<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .smembers(&key)
                .await
                .map(|values| values.into_iter().map(|value| value.to_vec()).collect::<Vec<_>>())
        })
    }

    /// Return the cardinality of the set at key.
    fn scard<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.scard(&key).await })
    }

    /// Add or update a sorted-set member and return whether it was newly added.
    fn zadd<'py>(
        &self,
        py: Python<'py>,
        key: String,
        score: f64,
        member: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.zadd(&key, score, &member).await })
    }

    /// Return sorted-set members matching pattern.
    fn zscan_match<'py>(
        &self,
        py: Python<'py>,
        key: String,
        pattern: String,
        count: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let count = score_limit(count)?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.zscan_match(&key, &pattern, count).await })
    }

    /// Return sorted-set members in the inclusive score range.
    fn zrangebyscore<'py>(
        &self,
        py: Python<'py>,
        key: String,
        min_score: f64,
        max_score: f64,
        limit: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let limit = score_limit(limit)?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .zrangebyscore(&key, min_score, max_score, limit)
                .await
        })
    }

    /// Remove members from a sorted set and return the number removed.
    fn zrem<'py>(
        &self,
        py: Python<'py>,
        key: String,
        members: Vec<String>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.zrem(&key, &members).await })
    }

    /// Push byte values onto the head of a list and return its length.
    fn lpush<'py>(
        &self,
        py: Python<'py>,
        key: String,
        values: Vec<Vec<u8>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        let values = values.into_iter().map(Bytes::from).collect();
        self.future(py, async move { runtime.lpush(&key, values).await })
    }

    /// Push byte values onto the tail of a list and return its length.
    fn rpush<'py>(
        &self,
        py: Python<'py>,
        key: String,
        values: Vec<Vec<u8>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        let values = values.into_iter().map(Bytes::from).collect();
        self.future(py, async move { runtime.rpush(&key, values).await })
    }

    /// Pop up to count values from the head of a list.
    #[pyo3(signature = (key, count=None))]
    fn lpop<'py>(
        &self,
        py: Python<'py>,
        key: String,
        count: Option<i64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let count = optional_count(count)?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .lpop(&key, count)
                .await
                .map(|values| values.into_iter().map(|value| value.to_vec()).collect::<Vec<_>>())
        })
    }

    /// Pop up to count values from the tail of a list.
    #[pyo3(signature = (key, count=None))]
    fn rpop<'py>(
        &self,
        py: Python<'py>,
        key: String,
        count: Option<i64>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let count = optional_count(count)?;
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .rpop(&key, count)
                .await
                .map(|values| values.into_iter().map(|value| value.to_vec()).collect::<Vec<_>>())
        })
    }

    /// Return the length of the list at key.
    fn llen<'py>(&self, py: Python<'py>, key: String) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.llen(&key).await })
    }

    /// Return the inclusive list range from start through stop.
    fn lrange<'py>(
        &self,
        py: Python<'py>,
        key: String,
        start: i64,
        stop: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .lrange(&key, start, stop)
                .await
                .map(|values| values.into_iter().map(|value| value.to_vec()).collect::<Vec<_>>())
        })
    }

    /// Return one list element by index.
    fn lindex<'py>(
        &self,
        py: Python<'py>,
        key: String,
        index: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .lindex(&key, index)
                .await
                .map(|value| value.map(|value| value.to_vec()))
        })
    }

    /// Replace one list element by index.
    fn lset<'py>(
        &self,
        py: Python<'py>,
        key: String,
        index: i64,
        value: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.lset(&key, index, Bytes::from(value)).await })
    }

    /// Trim a list to the inclusive range from start through stop.
    fn ltrim<'py>(
        &self,
        py: Python<'py>,
        key: String,
        start: i64,
        stop: i64,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.ltrim(&key, start, stop).await })
    }

    /// Remove matching list elements and return the number removed.
    fn lrem<'py>(
        &self,
        py: Python<'py>,
        key: String,
        count: i64,
        value: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.lrem(&key, count, Bytes::from(value)).await })
    }

    /// Insert value before or after pivot and return the new list length.
    fn linsert<'py>(
        &self,
        py: Python<'py>,
        key: String,
        position: String,
        pivot: Vec<u8>,
        value: Vec<u8>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = ListInsertRequest {
            key,
            position: list_insert_position(&position)?,
            pivot: Bytes::from(pivot),
            value: Bytes::from(value),
        };
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.linsert(request).await })
    }

    /// Move one list element and return the moved bytes or None.
    fn lmove<'py>(
        &self,
        py: Python<'py>,
        source: String,
        destination: String,
        source_direction: String,
        destination_direction: String,
    ) -> PyResult<Bound<'py, PyAny>> {
        let request = ListMoveRequest {
            source,
            destination,
            source_direction: list_direction(&source_direction, "source_direction")?,
            destination_direction: list_direction(&destination_direction, "destination_direction")?,
        };
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move {
            runtime
                .lmove(request)
                .await
                .map(|value| value.map(|value| value.to_vec()))
        })
    }

    /// Register a named Redis Lua script without performing I/O.
    fn register_script(&self, script_id: String, redis_lua: String) -> PyResult<()> {
        self.runtime
            .register_script(script_id, redis_lua)
            .map_err(|error| cache_error_to_py(&self.instance, error))
    }

    /// Execute one registered script and return its recursive Python result.
    fn execute_script<'py>(
        &self,
        py: Python<'py>,
        script_id: String,
        keys: Vec<String>,
        args: Vec<Vec<u8>>,
    ) -> PyResult<Bound<'py, PyAny>> {
        let instance = self.instance.clone();
        let runtime = Arc::clone(&self.runtime);
        let args = args.into_iter().map(Bytes::from).collect();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let result = runtime
                .execute_script(ScriptRequest {
                    script_id,
                    keys,
                    args,
                })
                .await
                .map_err(|error| cache_error_to_py(&instance, error))?;
            let value = result
                .decode()
                .map_err(|error| cache_error_to_py(&instance, error))?;
            Python::attach(|py| script_value_to_py(py, value))
        })
    }

    /// Check cache provider health.
    fn ping<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let runtime = Arc::clone(&self.runtime);
        self.future(py, async move { runtime.ping().await })
    }

}
