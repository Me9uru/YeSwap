"""AetherSwap application package bootstrap."""

from pathlib import Path

from dotenv import load_dotenv


# Explicit process environment always wins over local file values.
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
