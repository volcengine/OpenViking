# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Vendor differences without converting or dropping protocol fields."""

import hashlib

import orjson

# Volcano Engine Ark and BytePlus ModelArk, its international edition, share
# paths and caching behavior.
ARK_VENDORS = {"ark", "byteplus"}
STABLE_PARAMETERS = (
    "model",
    "thinking",
    "reasoning_effort",
    "temperature",
    "top_p",
    "seed",
    "tools",
    "system",
    "instructions",
)


def parameter_fingerprint(body):
    return hashlib.sha256(
        orjson.dumps(
            {key: body[key] for key in STABLE_PARAMETERS if key in body},
            option=orjson.OPT_SORT_KEYS,
        )
    ).hexdigest()


async def apply_vendor(body, upstream, prepared, store):
    if upstream.get("vendor") not in ARK_VENDORS or prepared is None:
        return body
    body = dict(body)
    if prepared.protocol in {"chat", "responses"}:
        cache = prepared.root["vendor"]
        body["prompt_cache_key"] = cache["prompt_cache_key"]
        if parameter_fingerprint(prepared.original) != cache["parameters"]:
            prepared.metrics["degradation"] = "ark_cache_parameters_changed"
    return body
