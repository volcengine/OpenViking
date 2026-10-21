# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Ephemeral Studio probes using the same adapters as normal model calls."""

import asyncio
import contextlib
import time

from openviking.models.model_error import model_error_kind
from openviking_cli.utils.config.embedding_config import EmbeddingConfig
from openviking_cli.utils.config.vlm_config import VLMConfig

VLM_PROVIDERS = {"openai", "azure", "volcengine", "glm", "kimi", "litellm"}
EMBEDDING_PROVIDERS = {
    "openai",
    "azure",
    "volcengine",
    "jina",
    "gemini",
    "voyage",
    "dashscope",
    "minimax",
    "cohere",
    "litellm",
}


async def _embed_probe(settings):
    # Some adapters discover dimensions with synchronous network I/O in __init__.
    creation = asyncio.create_task(asyncio.to_thread(settings.get_embedder))
    instance = None
    try:
        try:
            instance = await asyncio.shield(creation)
        except asyncio.CancelledError:
            # A thread cannot be cancelled; close its client if it finishes late.
            def close_late(task):
                with contextlib.suppress(Exception):
                    task.result().close()

            creation.add_done_callback(close_late)
            raise
        result = await instance.embed_async("connection test")
        return bool(result.dense_vector or result.sparse_vector)
    finally:
        if instance is not None:
            with contextlib.suppress(Exception):
                instance.close()


async def probe_model(kind, config, fallback, thinking=None, mode="dense"):
    """Never publish the draft, use failover, or expose provider error payloads."""
    started = time.monotonic()
    instance = None
    provider = str(config.get("provider") or "").lower()
    providers = VLM_PROVIDERS if kind == "vlm" else EMBEDDING_PROVIDERS
    if provider not in providers:
        return {"ok": False, "code": "unsupported"}
    try:
        if kind == "vlm":
            parent = {
                **fallback,
                "credentials": [config],
                "backup": None,
                "max_retries": 0,
                "timeout": 20,
            }
            if thinking is not None:
                parent["thinking"] = thinking
            settings = VLMConfig.model_validate(parent)
            instance = settings.get_vlm_instance()
            result = await asyncio.wait_for(
                instance.get_completion_async(prompt="Reply only OK."), timeout=25
            )
            valid = (
                bool(result.strip())
                if isinstance(result, str)
                else bool(
                    getattr(result, "content", None) or getattr(result, "reasoning_content", None)
                )
            )
        else:
            settings = EmbeddingConfig.model_validate(
                {
                    mode: {**fallback, "credentials": [config]},
                    "max_retries": 0,
                }
            )
            valid = await asyncio.wait_for(_embed_probe(settings), timeout=25)
        if not valid:
            return {"ok": False, "code": "invalidResponse"}
        return {"ok": True, "ms": round((time.monotonic() - started) * 1000)}
    except (asyncio.TimeoutError, TimeoutError):
        return {"ok": False, "code": "timeout"}
    except (ValueError, SystemExit):
        return {"ok": False, "code": "invalid_request"}
    except Exception as error:
        kind = model_error_kind(error)
        code = kind.value if kind is not None else "failed"
        # Never return raw SDK messages: these can contain credentials or response bodies.
        return {
            "ok": False,
            "code": {
                "transient_network_error": "network",
                "transient_service_error": "failed",
                "unknown": "failed",
            }.get(code, code),
        }
    finally:
        if instance is not None:
            with contextlib.suppress(Exception):
                instance.close()
