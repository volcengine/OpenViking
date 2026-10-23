"""Opt-in kind fixtures: each scenario owns a namespace and all of its resources."""

import os
from pathlib import Path

import pytest

from .harness import Harness


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "standalone: scenario excluded from the Redis Cluster matrix"
    )


def pytest_generate_tests(metafunc):
    if "harness" in metafunc.fixturenames:
        modes = os.getenv("OV_TEST_KIND_REDIS", "standalone,cluster").split(",")
        if any(mode not in {"standalone", "cluster"} for mode in modes):
            raise pytest.UsageError(
                "OV_TEST_KIND_REDIS must be standalone, cluster, or both separated by a comma"
            )
        metafunc.parametrize("harness", modes, indirect=True)


def pytest_collection_modifyitems(config, items):
    excluded = [
        item
        for item in items
        if item.get_closest_marker("standalone") and item.callspec.params["harness"] == "cluster"
    ]
    if excluded:
        config.hook.pytest_deselected(items=excluded)
        items[:] = [item for item in items if item not in excluded]


@pytest.fixture
def harness(request, tmp_path):
    kubeconfig = os.getenv("OV_TEST_KIND_KUBECONFIG")
    if not kubeconfig:
        pytest.skip("set OV_TEST_KIND_KUBECONFIG to run the isolated kind suite")
    assert Path(kubeconfig).is_file(), f"kubeconfig does not exist: {kubeconfig}"
    instance = Harness(kubeconfig, request.param, tmp_path)
    try:
        instance.start()
        yield instance
    finally:
        instance.collect_logs()
        instance.close()
