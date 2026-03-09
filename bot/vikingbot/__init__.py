"""
vikingbot - A lightweight AI agent framework
"""

import warnings

__version__ = "0.1.3"
__logo__ = "🐈"

# Suppress RequestsDependencyWarning from requests module
# This is safe - urllib3 2.x and chardet 7.x actually work fine with requests 2.32.5

# First, add a filter that works even if requests isn't imported yet
warnings.filterwarnings(
    "ignore",
    message="urllib3 (.*) or chardet (.*)/charset_normalizer (.*) doesn't match a supported version!",
    module="requests",
)

# Then try to add a more precise filter using the actual warning class
try:
    from requests.exceptions import RequestsDependencyWarning

    warnings.filterwarnings("ignore", category=RequestsDependencyWarning, module="requests")
except ImportError:
    pass
