# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Use a subprocess because freezing is process-global."""

import subprocess
import sys


def test_freeze_lifespan_preserves_gc_and_releases_after_error():
    subprocess.run(
        [
            sys.executable,
            "-c",
            """
import gc
import weakref
from openviking.server.startup_gc import startup_gc_freeze
threshold = gc.get_threshold()
initial = gc.get_freeze_count()
with startup_gc_freeze(False):
    assert gc.get_freeze_count() == initial
# Some Python distributions freeze startup objects themselves. This isolated
# subprocess owns its GC state; normalize before testing our ownership.
gc.unfreeze()
try:
    with startup_gc_freeze(True):
        assert gc.get_freeze_count() > 0
        assert gc.isenabled()
        assert gc.get_threshold() == threshold
        class Node:
            pass
        node = Node()
        node.cycle = node
        reference = weakref.ref(node)
        del node
        gc.collect()
        assert reference() is None
        try:
            with startup_gc_freeze(True):
                raise AssertionError("overlapping ownership accepted")
        except RuntimeError:
            pass
        raise ValueError("lifespan failed")
except ValueError:
    pass
assert gc.get_freeze_count() == 0
with startup_gc_freeze(True):
    assert gc.get_freeze_count() > 0
gc.freeze()
try:
    with startup_gc_freeze(True):
        raise AssertionError("external ownership accepted")
except RuntimeError:
    assert gc.get_freeze_count() > 0
finally:
    gc.unfreeze()
""",
        ],
        check=True,
    )
