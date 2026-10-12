"""Locations of mutable application data, independent of source layout."""

import os
from pathlib import Path

DATA_DIR = (
    Path(os.environ.get("AETHERSWAP_DATA_DIR", Path(__file__).resolve().parent.parent / "config"))
    .expanduser()
    .resolve()
)
