use super::*;

#[test]
fn detach_blocking_helper_runs_without_python_objects() {
    Python::attach(|py| {
        let value: i32 = py_detach_blocking(py, || 40 + 2);
        assert_eq!(value, 42);
    });
}

#[test]
fn constructor_accepts_canonical_config_path_with_runtime_service_name() {
    let path = std::env::temp_dir().join(format!(
        "openviking-canonical-config-path-{}.json",
        std::process::id()
    ));
    fs::write(
        &path,
        r#"{"service_name": "test-service", "storage": {"agfs": {"backend": "local"}}}"#,
    )
    .unwrap();

    Python::initialize();
    Python::attach(|py| {
        let ty = py.get_type::<RAGFSBindingClient>();
        let config = PyDict::new(py);
        config.set_item("service_name", "test-service").unwrap();
        let kwargs = PyDict::new(py);
        kwargs
            .set_item("config_path", path.to_str().unwrap())
            .unwrap();
        kwargs.set_item("config", config).unwrap();
        let instance = ty.call((), Some(&kwargs));
        assert!(instance.is_ok());
    });

    fs::remove_file(path).unwrap();
}
