import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[4]


def _read(relative_path: str) -> str:
    return (_ROOT / relative_path).read_text(encoding="utf-8")


def test_runtime_modules_use_unified_get_logger():
    target_files = [
        "openviking/server/oauth/router.py",
        "openviking/server/oauth/storage.py",
        "openviking/server/oauth/provider.py",
        "openviking/server/routers/sessions.py",
        "openviking/server/routers/stats.py",
        "openviking/observability/http_observability_middleware.py",
        "openviking/observability/context.py",
        "openviking/metrics/global_api.py",
        "openviking/metrics/exporters/otel.py",
        "openviking/parse/registry.py",
        "openviking/parse/tree_builder.py",
        "openviking/parse/accessors/registry.py",
        "openviking/parse/parsers/pdf.py",
        "openviking/parse/parsers/code/ast/extractor.py",
        "openviking/models/rerank/base.py",
        "openviking/models/rerank/volcengine_rerank.py",
        "openviking/models/rerank/openai_rerank.py",
        "openviking/models/rerank/litellm_rerank.py",
        "openviking/models/rerank/cohere_rerank.py",
        "openviking/models/embedder/base.py",
        "openviking/models/embedder/jina_embedders.py",
        "openviking/models/embedder/cohere_embedders.py",
        "openviking/models/embedder/openai_embedders.py",
        "openviking/models/embedder/voyage_embedders.py",
        "openviking/models/embedder/litellm_embedders.py",
        "openviking/models/embedder/local_embedders.py",
        "openviking/models/embedder/gemini_embedders.py",
        "openviking/models/vlm/base.py",
        "openviking/models/vlm/backends/litellm_vlm.py",
        "openviking/models/vlm/backends/volcengine_vlm.py",
        "openviking/models/vlm/backends/openai_vlm.py",
        "openviking_cli/session/user_id.py",
    ]

    for relative_path in target_files:
        content = _read(relative_path)
        assert "get_logger(__name__)" in content, relative_path
        assert "logging.getLogger(__name__)" not in content, relative_path
