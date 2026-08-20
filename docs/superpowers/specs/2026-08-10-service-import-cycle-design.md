# Service Package Circular Import Fix

## Problem

Importing a session training submodule in a fresh Python process currently fails:

```python
from openviking.session.train.components.progress import ProgressSummaryColumn
```

The import enters `openviking.storage.queuefs`, whose `named_queue` module imports
`openviking.service.task_work_index`. Python initializes the parent
`openviking.service` package first. Its eager exports import `resource_service`,
which asks the still-partially-initialized QueueFS package for `QueueManager` and
raises a circular-import error.

## Options

1. Make `openviking.service` exports lazy. This follows the existing patterns in
   `openviking.core`, `openviking.storage`, and `openviking.client`, preserves the
   public API, and changes only the package entry point.
2. Move `task_work_index` into QueueFS and retain a compatibility shim. This
   improves dependency direction but touches many imports and broadens the fix.
3. Defer the imports inside QueueFS methods. This breaks the immediate cycle but
   hides the dependency until runtime and is harder to maintain.

## Design

Use option 1. Replace eager imports in `openviking/service/__init__.py` with:

- `TYPE_CHECKING` imports for static analysis;
- a map from each existing public name to its defining module;
- module-level `__getattr__` that imports and caches an export on first access;
- `__dir__` and the unchanged `__all__` list for discoverability and compatibility.

No service class, QueueFS implementation, or benchmark code changes. Existing
imports such as `from openviking.service import OpenVikingService` retain their
behavior, while importing `openviking.service.task_work_index` no longer loads
the whole service layer.

## Verification

Add an isolated-process regression test so prior imports in the pytest process
cannot mask the order-dependent bug. The subprocess will verify, in a fresh
interpreter:

- the LoCoMo-triggering `ProgressSummaryColumn` import;
- direct `QueueManager` and `VikingDBManager` imports;
- existing top-level Service exports and service-submodule imports.

Run the focused regression test, relevant service/QueueFS tests, Ruff, and the
original import command before committing.
