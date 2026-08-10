#!/usr/bin/env python3
"""Version 4 demo: planner chooses MCP tools to answer a FinOps question.

Defaults to the offline planner (no API key). Pass --planner openai when
OPENAI_API_KEY is set for a real LLM.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from finops_agent.llm_agent import LLMFinOpsAgent  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="V4 FinOps LLM/offline planner demo")
    p.add_argument(
        "question",
        nargs="?",
        default="Where is our cloud money going, and are there anomalies or waste?",
    )
    p.add_argument("--planner", choices=["auto", "offline", "openai"], default="auto")
    p.add_argument("--model", default="gpt-4o-mini")
    p.add_argument("--json", action="store_true")
    return p


async def run(question: str, planner: str, model: str, as_json: bool) -> int:
    agent = LLMFinOpsAgent(planner_kind=planner, model=model)
    print(f"Planner={getattr(agent.planner, 'name', planner)}", file=sys.stderr)
    trace = await agent.ask(question)
    if as_json:
        print(json.dumps(trace.to_dict(), indent=2, default=str))
    else:
        print(agent.format_markdown(trace))
    return 0


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    raise SystemExit(
        asyncio.run(run(args.question, args.planner, args.model, args.json))
    )


if __name__ == "__main__":
    main()
