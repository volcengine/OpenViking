use crate::cache_runtime_handle::PyCacheRuntimeHandle;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use ragfs::cache_runtime::{
    CacheRuntime, MemoryMockProvider, ScriptRequest, ScriptResult, ScriptValue,
};
use std::sync::Arc;

/// Verify the async cache handle exposes every provider operation family.
#[test]
fn cache_runtime_handle_exposes_every_operation_family() {
    Python::initialize();
    let provider = Arc::new(MemoryMockProvider::new().with_script_handler(
        |request: ScriptRequest| {
            assert_eq!(request.script_id, "test.script.v1");
            assert_eq!(request.keys, vec!["key".to_string()]);
            assert_eq!(request.args, vec![b"argument".as_slice()]);
            ScriptResult::encode(&ScriptValue::Array(vec![
                ScriptValue::Integer(7),
                ScriptValue::Bytes(b"result".to_vec()),
                ScriptValue::Boolean(true),
            ]))
        },
    ));
    tokio::runtime::Runtime::new()
        .unwrap()
        .block_on(provider.insert_set_members("members", vec![b"member".to_vec().into()]));
    let runtime = CacheRuntime::memory_with_provider(provider);

    Python::attach(|py| -> PyResult<()> {
        let handle = Py::new(
            py,
            PyCacheRuntimeHandle::new("default".to_string(), Arc::clone(&runtime)),
        )?;
        assert_eq!(
            handle
                .bind(py)
                .getattr("default_ttl_seconds")?
                .extract::<Option<f64>>()?,
            Some(300.0)
        );
        assert!(handle.bind(py).getattr("sync").is_err());

        let locals = PyDict::new(py);
        locals.set_item("handle", &handle)?;
        py.run(
            c"
import asyncio

async def exercise(handle):
    assert await handle.set('key', b'value', if_not_exists=True)
    assert not await handle.set('key', b'replacement', if_not_exists=True)
    assert await handle.get('key') == b'value'
    for operation in (
        lambda: handle.set('ttl', b'value', ttl_seconds=1e300),
        lambda: handle.expire('ttl', 1e300),
    ):
        try:
            await operation()
        except ValueError:
            pass
        else:
            raise AssertionError('unrepresentable TTL was accepted')
    assert await handle.hset('hash', 'field', b'value') == 1
    assert await handle.hget('hash', 'field') == b'value'
    assert await handle.sismember('members', b'member')
    assert await handle.zadd('scores', 1.0, 'member') == 1
    assert await handle.zrangebyscore('scores', 0.0, 2.0, 10) == ['member']
    assert await handle.rpush('list', [b'first', b'second']) == 2
    assert await handle.lrange('list', 0, -1) == [b'first', b'second']
    handle.register_script('test.script.v1', 'return ARGV[1]')
    handle.register_script('test.script.v1', 'return ARGV[1]')
    try:
        handle.register_script('test.script.v1', 'return ARGV[2]')
    except ValueError:
        pass
    else:
        raise AssertionError('conflicting script registration succeeded')
    assert await handle.execute_script(
        'test.script.v1', ['key'], [b'argument']
    ) == [7, b'result', True]

asyncio.run(exercise(handle))
",
            None,
            Some(&locals),
        )?;

        assert!(handle.bind(py).getattr("close").is_err());
        Ok(())
    })
    .unwrap();
}
