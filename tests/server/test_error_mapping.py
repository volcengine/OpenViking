# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0

"""Focused tests for HTTP server exception-to-error mapping."""

from openviking.pyagfs.exceptions import AGFSClientError
from openviking.server.error_mapping import map_exception
from openviking_cli.exceptions import InvalidURIError, NotFoundError


def test_agfs_client_does_not_exist_maps_to_not_found():
    mapped = map_exception(
        AGFSClientError("path viking://missing does not exist"),
        resource="viking://missing",
        resource_type="file",
    )

    assert isinstance(mapped, NotFoundError)
    assert mapped.code == "NOT_FOUND"
    assert mapped.details == {"resource": "viking://missing", "type": "file"}


def test_agfs_client_invalid_uri_maps_to_invalid_uri():
    mapped = map_exception(
        AGFSClientError("Invalid URI: viking://"),
        resource="viking://",
    )

    assert isinstance(mapped, InvalidURIError)
    assert mapped.code == "INVALID_URI"
    assert mapped.details["uri"] == "viking://"


def test_value_error_invalid_uri_maps_to_invalid_uri():
    mapped = map_exception(ValueError("invalid viking URI: missing path"), resource="viking://")

    assert isinstance(mapped, InvalidURIError)
    assert mapped.code == "INVALID_URI"
