# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
"""
Model utilities - shared model conversion functions.
"""

from typing import Any, Dict


def model_to_dict(model: Any, exclude_none: bool = True) -> Dict[str, Any]:
    """Convert a model to a dictionary, handling both Pydantic models and raw dicts.

    Args:
        model: Pydantic model or dict to convert
        exclude_none: Whether to exclude None values (default: True)

    Returns:
        Dictionary representation of the model
    """
    if hasattr(model, 'model_dump'):
        if exclude_none:
            return model.model_dump(exclude_none=True)
        return model.model_dump()
    elif hasattr(model, 'dict'):
        # For backward compatibility with older Pydantic
        if exclude_none:
            return model.dict(exclude_none=True)
        return model.dict()
    else:
        return dict(model) if model else {}


# Backward compatibility alias
flat_model_to_dict = model_to_dict
