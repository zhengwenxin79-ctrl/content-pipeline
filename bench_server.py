from __future__ import annotations

import os
from pathlib import Path

from bench_analysis.web_app import run_server


def main() -> None:
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", os.environ.get("BENCH_PORT", "8765")))
    output_dir = Path(os.environ.get("BENCH_OUTPUT_DIR", "bench_analysis_outputs"))
    run_server(host=host, port=port, output_root=output_dir)


if __name__ == "__main__":
    main()
