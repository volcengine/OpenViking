from __future__ import annotations

import os
import subprocess
import sys


def test_base_star_import_omits_optional_middleware(tmp_path):
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    probe = """
import importlib.util

real_find_spec = importlib.util.find_spec
importlib.util.find_spec = (
    lambda name, *args, **kwargs:
    None if name == "langchain" else real_find_spec(name, *args, **kwargs)
)

import langchain_openviking

assert "OpenVikingContextMiddleware" not in langchain_openviking.__all__
namespace = {}
exec("from langchain_openviking import *", namespace)
assert "OpenVikingContextMiddleware" not in namespace
assert "OpenVikingRetriever" in namespace
"""

    subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
