"""Minimal Compile read tracing: a single audit hook auto-loaded in execs.

Only path constants live here; the hook itself is ``sitecustomize.py`` (the one
file Python auto-imports on startup from ``PYTHONPATH``).
"""

from pathlib import Path

TRACE_DIR = Path(__file__).resolve().parent
READLIST_REL_PATH = "__compile_staging__/tmp/readlist.md"

__all__ = ["READLIST_REL_PATH", "TRACE_DIR"]
