# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import json
import logging
from typing import Dict, List, Union


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
            logging.warning("try to load json failed: {}, p: {}".format(e, p))
            try:
                # Warning: This is a risky fallback for non-standard JSON using single quotes
                tp = p.replace("'", '"')
                temp = json.loads(tp)
            except json.JSONDecodeError as e:
                logging.error("try to load json after replace failed: {}, p: {}".format(e, p))
                if not no_exception:
                    raise ValueError("cannot convert_dict: {}".format(p))
                return {}
        if isinstance(temp, (dict, list)):
            return temp
        else:
            logging.error("convert_dict parse string failed: {} not dict".format(type(p)))
    return {}
