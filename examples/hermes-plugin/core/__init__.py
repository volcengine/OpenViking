"""Building blocks of the OpenViking Hermes plugin.

Modules in this package never import the plugin package itself (``__init__`` or
``_setup``). Only ``host.py`` imports Hermes; the other modules take Hermes names
from it. Importing any module here registers no atexit hook, starts no thread,
opens no socket and reads no configuration.
"""
