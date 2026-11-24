# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: AGPL-3.0
import json
from typing import Dict, List, Union

from openviking_cli.utils.logger import get_logger

logger = get_logger(__name__)


def convert_dict(p: Union[str, Dict, List], no_exception: bool = False) -> Union[Dict, List]:
    """
    Convert input to dictionary or list.
    If input is a string, try to parse it as JSON.

    Args:
        p: Input to convert (dict, list, or json string).
        no_exception: If True, return empty dict on error instead of raising ValueError.

    Returns:
        Converted dictionary or list. Returns empty dict if conversion fails and no_exception is True.
    """
    if not p:
        return {}
    if isinstance(p, (dict, list)):
        return p
    if isinstance(p, str):
        temp = {}
        try:
            temp = json.loads(p)
        except json.JSONDecodeError as e:
            logger.info(
                "JSON parsing failed; retrying with normalized quotes error_type=%s",
                type(e).__name__,
            )
            try:
                # Warning: This is a risky fallback for non-standard JSON using single quotes
                tp = p.replace("'", '"')
                temp = json.loads(tp)
            except json.JSONDecodeError as e:
                logger.info(
                    "JSON parsing rejected after quote normalization error_type=%s",
                    type(e).__name__,
                )
                if not no_exception:
                    raise ValueError("cannot convert_dict: {}".format(p))
                return {}
        if isinstance(temp, (dict, list)):
            return temp
        else:
            logger.info("JSON object required; rejecting value value_type=%s", type(p).__name__)
    return {}
