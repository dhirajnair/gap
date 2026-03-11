# SQL Agent package
from __future__ import annotations

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)


def init_env() -> None:
    """Load .env file explicitly. Call from entry points, not on import."""
    from dotenv import load_dotenv
    load_dotenv()
