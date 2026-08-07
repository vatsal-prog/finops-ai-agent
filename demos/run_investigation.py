#!/usr/bin/env python3
"""Quick demo: run the FinOps agent investigation and print the report."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finops_agent.agent import FinOpsAgent  # noqa: E402


def main() -> None:
    agent = FinOpsAgent()
    trace = agent.investigate(lookback_days=30)
    print(agent.format_markdown(trace))
    print("\n--- JSON trace (truncated) ---")
    import json

    data = trace.to_dict()
    # Keep output readable
    data["steps"] = data["steps"][:4] + [{"note": f"... {len(trace.steps) - 4} more steps ..."}]
    if data.get("report"):
        data["report"]["recommendations"] = data["report"]["recommendations"][:3]
    print(json.dumps(data, indent=2)[:4000])


if __name__ == "__main__":
    main()
