import hashlib
import os
from io import BytesIO

import pytest
from pydantic import ValidationError
from starlette.datastructures import UploadFile

from openviking.server.routers.resources import AddResourceRequest
from openviking.server.temp_upload_store import _stream_upload_to_local_temp


def test_if_changed_request_requires_temp_upload_exact_target_and_no_watch():
    with pytest.raises(ValidationError, match="requires 'temp_file_id'"):
        AddResourceRequest(
            path="https://example.com/source.md",
            to="viking://resources/source",
            if_changed=True,
        )
    with pytest.raises(ValidationError, match="requires an exact 'to' target"):
        AddResourceRequest(temp_file_id="upload.md", if_changed=True)
    with pytest.raises(ValidationError, match="not supported with resource watches"):
        AddResourceRequest(
            temp_file_id="upload.md",
            to="viking://resources/source",
            watch_interval=1,
            if_changed=True,
        )


@pytest.mark.asyncio
async def test_streamed_temp_upload_computes_source_fingerprint():
    content = b"stable source bytes"
    upload = UploadFile(BytesIO(content), filename="source.md")

    path, size, source_sha256 = await _stream_upload_to_local_temp(upload, 1024)
    try:
        assert size == len(content)
        assert source_sha256 == hashlib.sha256(content).hexdigest()
    finally:
        os.unlink(path)
