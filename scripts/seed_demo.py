"""Repository-facing entry point for the repeatable demonstration dataset."""
from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    source = Path(__file__).resolve().parents[1] / "tests" / "seed_demo_data.py"
    runpy.run_path(str(source), run_name="__main__")
