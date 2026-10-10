# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
"""Lazy optional NLP loading. Never install/download models in a server request."""

import threading
from functools import lru_cache

from openviking_cli.utils.config import get_openviking_config
from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)
_lock = threading.Lock()


@lru_cache(maxsize=4)
def _cached_model(name: str):
    try:
        import spacy

        return spacy.load(name)
    except (ImportError, OSError):
        logger.warning(
            "Cue linking unavailable: install openviking[nlp] and spaCy model %s; "
            "ordinary retrieval remains available",
            name,
        )
        return None


def _load_model(name: str):
    # Cache lookup belongs inside the lock: simultaneous first Add requests
    # must not all load a separate copy of the same NLP model.
    with _lock:
        return _cached_model(name)


def get_nlp_full():
    return _load_model(get_openviking_config().retrieval.memory_association.nlp_model)
