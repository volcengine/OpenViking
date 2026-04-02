"""
Entry point for running vikingbot as a module: python -m vikingbot
"""

import sys

from vikingbot.cli.commands import app

if __name__ == "__main__":
    # sys.argv = sys.argv + ['gateway']
    app()
