#!/usr/bin/env python3
"""Explicit mock bridge executable for offline integration tests."""
import argparse
import json
from pathlib import Path
from bridge_client import run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="TEST ONLY deterministic mock reader bridge")
    parser.add_argument("--config", required=True)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    config.update(adapter="mock", mock_delay=args.delay)
    raise SystemExit(run(config))
