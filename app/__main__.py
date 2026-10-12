"""
Entry point for ``python -m app``.
Equivalent to running ``uv run python run.py`` from the project root.
"""
from app.main import main

if __name__ == "__main__":
    main()
