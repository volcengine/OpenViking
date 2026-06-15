# TOS S3FS Backend Test Design

## Goal

Verify that OpenViking can use a real ByteDance TOS bucket as the AGFS backend through RAGFS `s3fs`.

The test should prove three things:

1. User-facing config `storage.agfs.backend = "s3"` is accepted for TOS.
2. RAGFS binding operations work against the real TOS bucket.
3. OpenViking service filesystem APIs work on top of that same TOS-backed AGFS.

## Assumptions

- The target object store is TOS using the S3-compatible endpoint.
- The bucket, region, access key, and secret key are provided outside the repo.
- Test data must live under an isolated prefix such as `openviking-e2e/RUN_ID`.
- The test may create and delete objects only under that isolated prefix.
- VectorDB and model-provider behavior are out of scope unless required to start the service.

## Selected Approach

Use a two-layer end-to-end test:

1. Run the existing AGFS binding S3 test suite with a TOS `ov.conf`.
2. Start OpenViking with the same config and run a small filesystem smoke test through the HTTP or CLI surface.

This is preferred over only validating config because it exercises real network I/O, directory markers, reads, listings, and deletion. It is also safer than broad ingestion tests because it avoids semantic processing, embedding calls, and large object creation.

## Alternatives Considered

### Config-only validation

This checks that OpenViking maps `backend = "s3"` to the Rust `s3fs` plugin. It is fast and safe, but it does not prove credentials, endpoint style, directory markers, or TOS object operations work.

### Binding-only test

This runs `tests/agfs/test_fs_binding_s3.py` with a real TOS config. It verifies core filesystem behavior and is the minimum useful live test. It does not verify the OpenViking server wiring above VikingFS.

### Full ingestion test

This would add resources and wait for indexing. It gives broader application confidence, but it introduces model provider, parser, queue, and vector-index variables. That extra surface is not needed to answer whether backendfs works as TOS-backed `s3fs`.

## TOS Configuration

Use a dedicated config file outside committed source, for example `/private/tmp/ov-tos-s3fs-e2e.conf`.

Recommended `storage.agfs` shape:

```json
{
  "backend": "s3",
  "timeout": 10,
  "s3": {
    "bucket": "tos-bucket-name",
    "region": "cn-beijing",
    "access_key": "local-tos-access-key-id",
    "secret_key": "local-tos-secret-access-key",
    "endpoint": "https://tos-s3-cn-beijing.volces.com",
    "prefix": "openviking-e2e/RUN_ID",
    "use_ssl": true,
    "use_path_style": false,
    "directory_marker_mode": "nonempty",
    "normalize_encoding_chars": "?#%+@"
  }
}
```

TOS-specific choices:

- `use_path_style: false` uses virtual-host-style requests, which matches the existing TOS example.
- `directory_marker_mode: "nonempty"` avoids relying on zero-byte directory markers.
- `bucket`, `access_key`, and `secret_key` are local-only values supplied by the operator before the run.
- `prefix` must be unique per run so cleanup is bounded and test objects never mix with production data.

## Test Flow

### 1. Binding smoke

Run the existing live S3 binding test:

```bash
OPENVIKING_CONFIG=/private/tmp/ov-tos-s3fs-e2e.conf pytest tests/agfs/test_fs_binding_s3.py -q
```

Expected result:

- The test is not skipped.
- File write, stat, ls, read, rm pass.
- Directory mkdir, nested write, tree, recursive rm pass.
- Binary write/read pass.

### 2. Service smoke

Start OpenViking with the same config, then perform filesystem operations through the public service surface:

1. Write a small text file under `viking://temp/tos-s3fs-e2e-RUN_ID.txt`.
2. `stat` the URI and verify it is a file.
3. `ls viking://temp/` and verify the file appears.
4. `read` the URI and verify exact content.
5. Delete the URI.
6. Confirm the URI is gone.

Expected result:

- OpenViking starts successfully with the TOS-backed AGFS config.
- Filesystem operations work through the server layer, not only the raw binding layer.

### 3. Optional TOS object check

If an S3-compatible client is available, list the isolated prefix after the write step and after cleanup.

Expected result:

- During the test, objects appear only under `openviking-e2e/RUN_ID`.
- After cleanup, no test file objects remain under that run prefix.

## Safety And Cleanup

- Never run the test with an empty, shared, or production prefix.
- Generate a new `RUN_ID` for each live run.
- Cleanup should delete only objects under the generated run prefix.
- Credentials must not be committed to the repo or printed in logs.
- If cleanup fails, report the exact prefix that needs manual cleanup.

## Success Criteria

The TOS `s3fs` backend test is successful when:

1. `tests/agfs/test_fs_binding_s3.py` passes against the real TOS config.
2. OpenViking starts with the same config.
3. A service-level filesystem write/read/list/delete smoke test passes.
4. Any created objects are removed, or the remaining isolated prefix is reported.

## Out Of Scope

- Adding new automated tests.
- Changing RAGFS or OpenViking runtime code.
- Benchmarking S3FS performance.
- Testing multi-write backup behavior.
- Testing RAGFS cache providers.
