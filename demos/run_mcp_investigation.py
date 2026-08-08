#!/usr/bin/env python3
"""Version 3 demo: MCP-powered FinOps investigation agent.

Connects to the finops-agent MCP server, discovers tools, runs the playbook
via call_tool, and prints a markdown report. No LLM. No direct analytics imports.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finops_agent.mcp_agent import MCPFinOpsAgent  # noqa: E402


async def run_demo(*, lookback_days: int = 30, as_json: bool = False) -> int:
    agent = MCPFinOpsAgent()
    print("Running V3 MCP FinOps investigation…", file=sys.stderr)
    trace = await agent.investigate(lookback_days=lookback_days)
    if as_json:
        print(json.dumps(trace.to_dict(), indent=2))
    else:
        print(agent.format_markdown(trace))
    return 0


def main() -> None:
    as_json = "--json" in sys.argv
    raise SystemExit(asyncio.run(run_demo(as_json=as_json)))


if __name__ == "__main__":
    main()
