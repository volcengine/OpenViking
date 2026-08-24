"""Trace file reads for Compile exec subprocesses.

``DirectBackend.execute`` prepends this directory to ``PYTHONPATH`` and sets
``READLIST_FILE``/``READLIST_WORKSPACE`` for Compile sessions, so every
``python3`` started inside a Compile exec imports this file (Python
auto-imports ``sitecustomize`` on startup) and records each source file the
interpreter opens. Bulk Python reads (``glob``-and-``open`` scripts, JSONL
scans, ...) become visible to the readlist instead of staying invisible.
"""

import os
import sys

_READLIST_ENV = "READLIST_FILE"
_WORKSPACE_ENV = "READLIST_WORKSPACE"


def _hook(event, args):
    if event not in ("open", "os.open"):
        return
    try:
        path = args[0]
        if not isinstance(path, str) or not path:
            return
        # Resolve against the workspace root (not cwd, which may be a subdir
        # after ``cd``), handling absolute/relative paths and symlinked roots.
        base = os.path.realpath(os.environ.get(_WORKSPACE_ENV) or os.getcwd())
        rel = os.path.relpath(os.path.realpath(path), base)
        if rel == ".." or rel.startswith("../") or not rel.startswith("compile_resources/"):
            return
        readlist = os.environ.get(_READLIST_ENV)
        if not readlist:
            return
        os.makedirs(os.path.dirname(readlist), exist_ok=True)
        with open(readlist, "a", encoding="utf-8") as fh:
            fh.write(rel + "\n")
    except Exception:
        # Audit hooks must never raise: a raised hook breaks the agent's command.
        pass


if os.environ.get(_READLIST_ENV):
    sys.addaudithook(_hook)
