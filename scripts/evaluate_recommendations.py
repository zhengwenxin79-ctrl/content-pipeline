#!/usr/bin/env python3
"""Compatibility wrapper for the benchmark package.

The original script name is kept so existing notes and manual commands do not
break. New work should call `python3 -m benchmarks.run_benchmark` directly.
"""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.run_benchmark import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
