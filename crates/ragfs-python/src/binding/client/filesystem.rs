#[pymethods]
impl RAGFSBindingClient {
    /// List directory contents.
    ///
    /// Returns a list of file info dicts with keys:
    /// name, size, mode, modTime, isDir
    #[pyo3(signature = (path, ctx=None, *, offset=0, limit=None, sort_by=None, sort_order=None))]
    fn ls(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
        offset: i64,
        limit: Option<i64>,
        sort_by: Option<&str>,
        sort_order: Option<&str>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let offset = parse_listing_offset(offset)?;
        let limit = parse_listing_limit(limit)?;
        let sort_by = parse_list_sort_by(sort_by)?;
        let sort_order = parse_sort_order(sort_order)?;
        let entries = self
            .run_scoped(py, fs_ctx, move || async move {
                top.read_dir(&path, Some(offset), limit, sort_by, sort_order)
                    .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = file_info_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Read file content.
    ///
    /// Args:
    ///     path: File path
    ///     offset: Starting position (default: 0)
    ///     size: Number of bytes to read (default: -1, read all)
    ///     stream: Not supported in binding mode
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn read(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let off = if offset < 0 { 0u64 } else { offset as u64 };
        let sz = if size < 0 { 0u64 } else { size as u64 };

        let data = self
            .run_scoped(
                py,
                fs_ctx,
                move || async move { top.read(&path, off, sz).await },
            )
            .map_err(to_py_err)?;

        Python::attach(|py| Ok(PyBytes::new(py, &data).into()))
    }

    /// Read file content (alias for read).
    #[pyo3(signature = (path, offset=0, size=-1, stream=false, ctx=None))]
    fn cat(
        &self,
        py: Python<'_>,
        path: String,
        offset: i64,
        size: i64,
        stream: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        self.read(py, path, offset, size, stream, ctx)
    }

    /// Write data to file.
    ///
    /// Registered write hooks run before the filesystem is touched. The
    /// reserved GLOBAL policy path is validated as a v1 policy; invalid content
    /// raises ``GlobalPolicyError`` (``GLOBAL_POLICY_INVALID``) or
    /// ``UnsupportedGlobalPolicySchemaError`` (``GLOBAL_POLICY_SCHEMA_UNSUPPORTED``).
    ///
    /// Args:
    ///     path: File path
    ///     data: File content as bytes
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    #[pyo3(signature = (path, data, max_retries=3, ctx=None))]
    fn write(
        &self,
        py: Python<'_>,
        path: String,
        data: Vec<u8>,
        max_retries: i32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<String> {
        let _ = max_retries; // not applicable for local binding
        self.write_hooks
            .check(&path, &data)
            .map_err(|error| global_policy::write_hook_error(py, error))?;
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let len = data.len();
        self.run_scoped(py, fs_ctx, move || async move {
            top.write(&path, &data, 0, WriteFlag::Create).await
        })
        .map_err(to_py_err)?;

        Ok(format!("Written {} bytes", len))
    }

    /// Create a new empty file.
    #[pyo3(signature = (path, ctx=None))]
    fn create(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move { top.create(&path).await })
            .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Create a directory.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn mkdir(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.mkdir(&path, mode_int).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "created".to_string());
        Ok(m)
    }

    /// Ensure all parent directories exist for the given path.
    #[pyo3(signature = (path, mode="755", ctx=None))]
    fn ensure_parent_dirs(
        &self,
        py: Python<'_>,
        path: String,
        mode: &str,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let mode_int = u32::from_str_radix(mode, 8)
            .map_err(|e| PyRuntimeError::new_err(format!("Invalid mode '{}': {}", mode, e)))?;

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.ensure_parent_dirs(&path, mode_int).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            "parent directories ensured".to_string(),
        );
        Ok(m)
    }

    /// Remove a file or directory.
    #[pyo3(signature = (path, recursive=false, ctx=None))]
    fn rm(
        &self,
        py: Python<'_>,
        path: String,
        recursive: bool,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            if recursive {
                top.remove_all(&path).await
            } else {
                top.remove(&path).await
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "deleted".to_string());
        Ok(m)
    }

    /// Get file/directory information.
    #[pyo3(signature = (path, ctx=None))]
    fn stat(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let info = self
            .run_scoped(py, fs_ctx, move || async move { top.stat(&path).await })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = file_info_to_py_dict(py, &info)?;
            Ok(dict.into())
        })
    }

    /// Rename/move a file or directory.
    #[pyo3(signature = (old_path, new_path, ctx=None))]
    fn mv(
        &self,
        py: Python<'_>,
        old_path: String,
        new_path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            top.rename(&old_path, &new_path).await
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "renamed".to_string());
        Ok(m)
    }

    /// Attempt a same-mount verbatim copy and report whether the fast-path was used.
    #[pyo3(signature = (src_path, dst_path, ctx=None))]
    fn copy_within_mount(
        &self,
        py: Python<'_>,
        src_path: String,
        dst_path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, bool>> {
        let fs_ctx = build_fs_context(ctx);
        let mountable = self.mountable.clone();
        let performed = self
            .run_scoped(py, fs_ctx, move || async move {
                mountable.copy_within_mount(&src_path, &dst_path).await
            })
            .map_err(to_py_err)?;

        let mut result = HashMap::new();
        result.insert("performed".to_string(), performed);
        Ok(result)
    }

    /// Change file permissions.
    #[pyo3(signature = (path, mode, ctx=None))]
    fn chmod(
        &self,
        py: Python<'_>,
        path: String,
        mode: u32,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(
            py,
            fs_ctx,
            move || async move { top.chmod(&path, mode).await },
        )
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "chmod ok".to_string());
        Ok(m)
    }

    /// Touch a file (create if not exists, or update timestamp).
    #[pyo3(signature = (path, ctx=None))]
    fn touch(
        &self,
        py: Python<'_>,
        path: String,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<HashMap<String, String>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        self.run_scoped(py, fs_ctx, move || async move {
            // Try create; if already exists, write empty to update mtime
            match top.create(&path).await {
                Ok(_) => Ok(()),
                Err(_) => {
                    let existing = top.read(&path, 0, 0).await?;
                    top.write(&path, &existing, 0, WriteFlag::Create).await?;
                    Ok(())
                }
            }
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), "touched".to_string());
        Ok(m)
    }

    /// List all mounted plugins.
    fn mounts(&self, py: Python<'_>) -> PyResult<Vec<HashMap<String, String>>> {
        let mountable = self.mountable.clone();
        let mount_list = py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.list_mounts().await })
        });

        let result: Vec<HashMap<String, String>> = mount_list
            .into_iter()
            .map(|(path, fstype)| {
                let mut m = HashMap::new();
                m.insert("path".to_string(), path);
                m.insert("fstype".to_string(), fstype);
                m
            })
            .collect();

        Ok(result)
    }

    /// Mount a plugin dynamically.
    ///
    /// Args:
    ///     fstype: Filesystem type (e.g., "memfs", "sqlfs", "kvfs", "queuefs")
    ///     path: Mount path
    ///     config: Plugin configuration as dict. For multi-write, include:
    ///         - "backups": nested dict matching BackendsConfig schema
    ///         - "server_encryption_enabled": bool
    ///         - "primary_encryption_enabled": bool
    #[pyo3(signature = (fstype, path, config=None))]
    fn mount(
        &self,
        py: Python<'_>,
        fstype: String,
        path: String,
        config: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<HashMap<String, String>> {
        let params = match config {
            Some(dict) => py_dict_to_config(dict)?,
            None => HashMap::new(),
        };
        let plugin_config = PluginConfig::from_raw_parts(fstype.clone(), path.clone(), params)
            .map_err(to_py_err)?;

        let mountable = self.mountable.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.mount(plugin_config).await })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert(
            "message".to_string(),
            format!("mounted {} at {}", fstype, path),
        );
        Ok(m)
    }

    /// Unmount a plugin.
    fn unmount(&self, py: Python<'_>, path: String) -> PyResult<HashMap<String, String>> {
        let mountable = self.mountable.clone();
        let path_clone = path.clone();
        py_detach_blocking(py, move || {
            self.rt
                .block_on(async move { mountable.unmount(&path_clone).await })
        })
        .map_err(to_py_err)?;

        let mut m = HashMap::new();
        m.insert("message".to_string(), format!("unmounted {}", path));
        Ok(m)
    }

    /// List all registered plugin names.
    fn list_plugins(&self) -> PyResult<Vec<String>> {
        // Return names of built-in plugins
        let mut plugins = vec![
            "memfs".to_string(),
            "kvfs".to_string(),
            "queuefs".to_string(),
            "sqlfs".to_string(),
            "localfs".to_string(),
            "serverinfofs".to_string(),
        ];
        #[cfg(feature = "s3")]
        plugins.push("s3fs".to_string());
        Ok(plugins)
    }

    /// Get detailed plugin information.
    fn get_plugins_info(&self) -> PyResult<Vec<String>> {
        self.list_plugins()
    }

    /// Load an external plugin (not supported in Rust binding).
    fn load_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin loading not supported in ragfs-python binding",
        ))
    }

    /// Unload an external plugin (not supported in Rust binding).
    fn unload_plugin(&self, _library_path: String) -> PyResult<HashMap<String, String>> {
        Err(PyRuntimeError::new_err(
            "External plugin unloading not supported in ragfs-python binding",
        ))
    }

    /// Search for pattern in files using regular expressions.
    ///
    /// Args:
    ///     path: File or directory path to search
    ///     pattern: Regular expression pattern to search for
    ///     recursive: Whether to search recursively in subdirectories (default: false)
    ///     case_insensitive: Whether to perform case-insensitive matching (default: false)
    ///     stream: Not supported in binding mode
    ///     node_limit: Maximum number of matches to return (default: None, no limit)
    ///     exclude_path: Optional path prefix to exclude from search (default: None)
    ///     level_limit: Optional maximum depth relative to query root (default: None)
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///     before_context: Number of lines to include before each match (default: 0)
    ///     after_context: Number of lines to include after each match (default: 0)
    ///
    /// Returns:
    ///     A dict with "matches" (list of match dicts) and "count" (total matches)
    #[pyo3(signature = (path, pattern, recursive=false, case_insensitive=false, stream=false, node_limit=None, exclude_path=None, level_limit=None, ctx=None, before_context=0, after_context=0))]
    #[allow(clippy::too_many_arguments)]
    fn grep(
        &self,
        py: Python<'_>,
        path: String,
        pattern: String,
        recursive: bool,
        case_insensitive: bool,
        stream: bool,
        node_limit: Option<i32>,
        exclude_path: Option<String>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
        before_context: i32,
        after_context: i32,
    ) -> PyResult<Py<PyAny>> {
        if stream {
            return Err(PyRuntimeError::new_err(
                "Streaming not supported in binding mode",
            ));
        }

        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let before_context = before_context.max(0) as usize;
        let after_context = after_context.max(0) as usize;

        let result = self
            .run_scoped(py, fs_ctx, move || async move {
                top.grep(
                    &path,
                    &pattern,
                    GrepOptions {
                        recursive,
                        case_insensitive,
                        node_limit: limit,
                        exclude_path: exclude_path.as_deref(),
                        level_limit: level_limit_usize,
                        before_context,
                        after_context,
                    },
                )
                .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let dict = grep_result_to_py_dict(py, &result)?;
            Ok(dict.into())
        })
    }

    /// Recursively traverse a directory tree.
    ///
    /// Args:
    ///     path: The root path of the traversal
    ///     show_hidden: Whether to include hidden files (default: False)
    ///     node_limit: Maximum number of nodes to return (default: None, no limit)
    ///     level_limit: Maximum depth relative to query root (default: None, no limit)
    ///     directories_only: Whether to return only directory entries (default: False)
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A list of dicts, each with keys: path, rel_path, info, extra
    #[pyo3(signature = (path, show_hidden=false, node_limit=None, level_limit=None, ctx=None, *, offset=0, sort_by=None, sort_order=None, directories_only=false))]
    fn tree_directory(
        &self,
        py: Python<'_>,
        path: String,
        show_hidden: bool,
        node_limit: Option<i32>,
        level_limit: Option<i32>,
        ctx: Option<HashMap<String, String>>,
        offset: i64,
        sort_by: Option<&str>,
        sort_order: Option<&str>,
        directories_only: bool,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let limit = node_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });
        let offset = parse_listing_offset(offset)?;
        let sort_by = parse_list_sort_by(sort_by)?;
        let sort_order = parse_sort_order(sort_order)?;

        let entries = self
            .run_scoped(py, fs_ctx, move || async move {
                top.tree_directory(
                    &path,
                    show_hidden,
                    limit,
                    level_limit_usize,
                    Some(offset),
                    sort_by,
                    sort_order,
                    directories_only,
                )
                .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let list = PyList::empty(py);
            for entry in &entries {
                let dict = tree_entry_to_py_dict(py, entry)?;
                list.append(dict)?;
            }
            Ok(list.into())
        })
    }

    /// Return one page of flat glob results.
    ///
    /// Args:
    ///     path: The root path of the traversal
    ///     pattern: Glob pattern matched against query-root-relative paths
    ///     show_hidden: Whether to include hidden files (default: False)
    ///     page_size: Maximum number of matched entries returned in this page
    ///     level_limit: Maximum depth relative to query root (default: None)
    ///     continuation_token: Opaque token returned by the previous page
    ///     ctx: Optional FsContext dict (e.g. {"account_id": ...})
    ///
    /// Returns:
    ///     A dict with keys: entries (list[GlobEntry]), next_token (str | None)
    #[pyo3(signature = (path, pattern, show_hidden=false, page_size=None, level_limit=None, continuation_token=None, ctx=None))]
    fn glob_directory(
        &self,
        py: Python<'_>,
        path: String,
        pattern: String,
        show_hidden: bool,
        page_size: Option<i32>,
        level_limit: Option<i32>,
        continuation_token: Option<String>,
        ctx: Option<HashMap<String, String>>,
    ) -> PyResult<Py<PyAny>> {
        let fs_ctx = build_fs_context(ctx);
        let top = self.top.clone();
        let page_size = page_size.map(|n| if n < 0 { 0 } else { n as usize });
        let level_limit_usize = level_limit.map(|n| if n < 0 { 0 } else { n as usize });

        let page: GlobPage = self
            .run_scoped(py, fs_ctx, move || async move {
                top.glob_directory(
                    &path,
                    &pattern,
                    show_hidden,
                    page_size,
                    level_limit_usize,
                    continuation_token,
                )
                .await
            })
            .map_err(to_py_err)?;

        Python::attach(|py| {
            let value = serde_json::to_value(&page)
                .map_err(|err| PyRuntimeError::new_err(err.to_string()))?;
            serde_json_to_py(py, &value)
        })
    }

    /// Calculate file digest (not yet implemented in ragfs).
    #[pyo3(signature = (path, algorithm="xxh3"))]
    fn digest(&self, path: String, algorithm: &str) -> PyResult<HashMap<String, String>> {
        let _ = (path, algorithm);
        Err(PyRuntimeError::new_err(
            "digest not yet implemented in ragfs-python",
        ))
    }

}
