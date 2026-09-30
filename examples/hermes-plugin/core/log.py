"""The plugin logger shared by the core modules.

Records keep the name of the provider's module, the plugin package, which is what
users and tests filter on. Hermes loads the package under a namespace of its own
(``_hermes_user_memory.<name>`` for an installed plugin), so the name is taken
from this module's package instead of being written out.
"""

from __future__ import annotations

import logging

_PLUGIN_PACKAGE = (__package__ or __name__).rpartition(".")[0] or __name__


def get_logger() -> logging.Logger:
    """The logger ``__init__`` gets from ``logging.getLogger(__name__)``."""
    return logging.getLogger(_PLUGIN_PACKAGE)
