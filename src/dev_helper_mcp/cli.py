"""Command-line entry point.

Minimal here by design: the server is global, so there is NO ``--repo`` flag.
A ``--port`` override is accepted; full strict-override / ``stop`` lifecycle
semantics are Story 3.2.
"""

import argparse
import logging
import os

from .config import APP_NAME
from . import server


def _configure_logging() -> None:
    level = os.environ.get("DEV_HELPER_LOG", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    parser = argparse.ArgumentParser(prog=APP_NAME, description="Global dev-helper MCP server")
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind to this exact port instead of scanning the default range.",
    )
    args = parser.parse_args()

    _configure_logging()
    server.run(port=args.port)


if __name__ == "__main__":
    main()
