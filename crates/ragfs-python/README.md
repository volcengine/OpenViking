# ragfs-python

PyO3 bindings for the RAGFS filesystem engine.

## Development

Check the crate from the repository root:

```bash
cargo check -p ragfs-python
```

`ragfs_python.pyi` is generated from the annotated PyO3 API and is committed to
the repository. After changing a `#[pyfunction]`, `#[pyclass]`, or
`#[pymethods]` interface, regenerate and format it:

```bash
cargo run --locked -p ragfs-python \
  --bin stub_gen \
  --no-default-features
uvx --from ruff==0.15.5 ruff format crates/ragfs-python/ragfs_python.pyi
uvx --from ruff==0.15.5 ruff check --fix crates/ragfs-python/ragfs_python.pyi
```

The generator must link to a shared Python library. If the selected interpreter
uses a static library, point PyO3 at a shared Python installation:

```bash
PYO3_PYTHON=/usr/bin/python3 cargo run --locked -p ragfs-python \
  --bin stub_gen \
  --no-default-features
```

Do not edit `ragfs_python.pyi` manually. Add `#[gen_stub(...)]` type overrides
to the Rust declaration when the generated Python type needs refinement. CI
regenerates and formats the stub and fails when it differs from the committed
file.
