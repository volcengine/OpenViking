# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Native listing batches preserve ordering and belong to one enumeration."""

import os

import pytest

from openviking.utils.agfs_utils import RagfsBindingConfig, create_agfs_client
from openviking_cli.utils.config.agfs_config import AGFSConfig


@pytest.fixture
def listing_client(tmp_path):
    root = tmp_path / "viking" / "acct" / "resources"
    root.mkdir(parents=True)
    for index in range(270):
        path = root / f"file-{index:03d}.md"
        path.write_text(str(index))
        os.utime(path, (1700000000 + index, 1700000000 + index))
    for name in ["a-dir", "Z-dir", ".hidden-dir"]:
        (root / name).mkdir()
        (root / name / "child.md").write_text(name)
    (root / ".hidden.md").write_text("hidden")
    (root / ".path.ovlock").write_text("internal")
    client = create_agfs_client(RagfsBindingConfig(agfs=AGFSConfig(path=str(tmp_path))))
    yield client, root
    client.close()


@pytest.mark.parametrize("sort_by,sort_order", [(None, "asc"), ("name", "desc"), ("mtime", "asc")])
@pytest.mark.parametrize("page_size", [1, 256, None])
@pytest.mark.parametrize("offset", [0, 256, 1000])
def test_ls_batches_equal_unpaged_result(listing_client, sort_by, sort_order, page_size, offset):
    client, _ = listing_client
    kwargs = {"ctx": {"account_id": "acct"}, "sort_by": sort_by, "sort_order": sort_order}
    expected = client.ls("/local/acct/resources", offset=offset, **kwargs)
    pages = list(
        client.ls_pages("/local/acct/resources", page_size=page_size, offset=offset, **kwargs)
    )
    assert [entry for page in pages for entry in page] == expected
    assert all(0 < len(page) <= (page_size or len(expected)) for page in pages)
    assert ".path.ovlock" not in {entry["name"] for entry in expected}


@pytest.mark.parametrize(
    "options",
    [
        {},
        {"show_hidden": True, "sort_by": "name", "sort_order": "desc"},
        {"level_limit": 1, "sort_by": "mtime"},
        {"directories_only": True, "show_hidden": True},
    ],
)
@pytest.mark.parametrize("offset", [0, 256, 1000])
def test_tree_batches_equal_unpaged_result(listing_client, options, offset):
    client, _ = listing_client
    kwargs = {"ctx": {"account_id": "acct"}, **options}
    expected = client.tree_directory("/local/acct/resources", offset=offset, **kwargs)
    pages = list(
        client.tree_directory_pages("/local/acct/resources", page_size=2, offset=offset, **kwargs)
    )
    assert [entry for page in pages for entry in page] == expected
    assert all(0 < len(page) <= 2 for page in pages)


@pytest.mark.parametrize("method", ["ls_pages", "tree_directory_pages"])
def test_listing_is_not_shared_between_calls(listing_client, method):
    client, root = listing_client
    enumerate_pages = getattr(client, method)
    kwargs = {"page_size": 2, "ctx": {"account_id": "acct"}}
    first = enumerate_pages("/local/acct/resources", **kwargs)
    (root / "new.md").write_text("new")
    second = enumerate_pages("/local/acct/resources", **kwargs)

    def names(pages):
        return {entry.get("info", entry)["name"] for page in pages for entry in page}

    assert "new.md" not in names(first)
    assert "new.md" in names(second)


@pytest.mark.parametrize("method", ["ls_pages", "tree_directory_pages"])
def test_listing_batches_empty_and_invalid_size(listing_client, method):
    client, root = listing_client
    (root / "empty").mkdir()
    enumerate_pages = getattr(client, method)
    kwargs = {"ctx": {"account_id": "acct"}}
    assert list(enumerate_pages("/local/acct/resources/empty", page_size=256, **kwargs)) == []
    with pytest.raises(ValueError, match="page_size must be positive"):
        enumerate_pages("/local/acct/resources", page_size=0, **kwargs)
