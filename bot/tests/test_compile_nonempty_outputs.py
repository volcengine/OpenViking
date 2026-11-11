import pytest
from vikingbot.compile import file_ops
from vikingbot.compile.results import FileDraft, FileResponse, Group

from bot.tests.test_compile_sources import runtime as runtime


@pytest.mark.parametrize("body", ["", " \n\t", b"", b" \n", "\u2003"])
def test_empty_non_markdown_output_rejected_before_staging(runtime, body):
    pipeline, _ = runtime
    pipeline.direct_output_format = "wiki"
    response = FileResponse(
        files=[FileDraft(path="OUT_DIR_PLACEHOLDER", **file_ops.encode_content(body))]
    )
    with pytest.raises(ValueError, match="Empty output: OUT_DIR_PLACEHOLDER"):
        file_ops.validate_files(pipeline, response, Group("direct", []), [], {})


@pytest.mark.parametrize("body", ["data", b"\xff\x00"])
def test_nonempty_non_markdown_output_remains_valid(runtime, body):
    pipeline, _ = runtime
    response = FileResponse(files=[FileDraft(path="asset", **file_ops.encode_content(body))])
    file_ops.validate_files(pipeline, response, Group("direct", []), [], {})
