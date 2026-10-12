"""
Project root entry point.
Usage:
    uv run python run.py   # from project root
    uv run python -m app   # from project root (equivalent)
"""
import sys
from pathlib import Path

# Ensure the project root is on sys.path when invoked directly
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.main import main

if __name__ == "__main__":
    main()
