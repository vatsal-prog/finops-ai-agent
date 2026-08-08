#!/usr/bin/env python3
"""Version 2 demo: MCP client connects to finops-agent server over stdio.

Discovers tools dynamically, then calls get_cost_breakdown(group_by="service").
Does not import analytics functions and does not use an LLM.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finops_agent.mcp_client import FinOpsMCPClient  # noqa: E402


async def run_demo() -> int:
    print("Connecting to finops-agent MCP server over stdio…")
    async with FinOpsMCPClient() as client:
        print(f"Connected to server: {client.server_name} v{client.server_version}")
        print()

        tools = await client.list_tools()
        print(f"Discovered {len(tools)} MCP tool(s):")
        for tool in tools:
            desc = (tool.description or "").splitlines()[0][:90]
            print(f"  - {tool.name}: {desc}")
        print()

        print('Calling get_cost_breakdown(group_by="service")…')
        result = await client.call_tool(
            "get_cost_breakdown",
            {"group_by": "service"},
        )
        print()
        print("Structured result:")
        print(json.dumps(result, indent=2))
        return 0


def main() -> None:
    raise SystemExit(asyncio.run(run_demo()))


if __name__ == "__main__":
    main()
