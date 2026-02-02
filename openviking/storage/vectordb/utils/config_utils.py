# Copyright (c) 2026 Beijing Volcano Engine Technology Co., Ltd.
# SPDX-License-Identifier: Apache-2.0
import os
from typing import Any, Dict, Optional


def get_config_value(
    config: Optional[Dict[str, Any]], config_key: str, env_var: str, default_value: Any
) -> Any:
    """
    Get config value by priority: config parameter → environment variable → default value

    Args:
        config: Configuration dictionary
        config_key: Key name in configuration dictionary
        env_var: Environment variable name
        default_value: Default value

    Returns:
        Configuration value
    """
    # Priority 1: Get from config parameter
    if config is not None and config_key in config:
        return config[config_key]

    # Priority 2: Get from environment variable
    env_value = os.environ.get(env_var)
    if env_value is not None:
        # Try to convert to numeric type (if default value is numeric)
        if isinstance(default_value, int):
            try:
                return int(env_value)
            except ValueError:
                pass
        elif isinstance(default_value, float):
            try:
                return float(env_value)
            except ValueError:
                pass
        return env_value

    # Priority 3: Use default value
    return default_value
