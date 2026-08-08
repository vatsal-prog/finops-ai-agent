"""CLI entrypoints for the FinOps agent demo."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from finops_agent.agent import FinOpsAgent
from finops_agent.analytics import (
    detect_anomaly,
    find_underutilized_resources,
    get_cost_breakdown,
    simulate_savings,
)
from finops_agent.mcp_agent import MCPFinOpsAgent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finops-agent",
        description="FinOps Cloud Cost Optimization Agent — analyze spend, detect anomalies, simulate savings.",
    )
    parser.add_argument(
        "--data",
        default=None,
        help="Path to billing JSON (defaults to bundled sample_billing.json)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    inv = sub.add_parser(
        "investigate",
        help="Run V1 agent investigation playbook (direct Python analytics)",
    )
    inv.add_argument("--lookback-days", type=int, default=30)
    inv.add_argument("--json", action="store_true", help="Emit machine-readable JSON trace")
    inv.add_argument("-o", "--output", help="Write report to file")

    inv_mcp = sub.add_parser(
        "investigate-mcp",
        help="Run V3 agent investigation over MCP (stdio client → MCP server)",
    )
    inv_mcp.add_argument("--lookback-days", type=int, default=30)
    inv_mcp.add_argument("--json", action="store_true", help="Emit machine-readable JSON trace")
    inv_mcp.add_argument("-o", "--output", help="Write report to file")

    bd = sub.add_parser("breakdown", help="Cost breakdown")
    bd.add_argument("--group-by", default="service", choices=["service", "region", "resource", "team", "env", "day"])
    bd.add_argument("--service")
    bd.add_argument("--region")
    bd.add_argument("--start-date")
    bd.add_argument("--end-date")

    an = sub.add_parser("anomalies", help="Detect cost anomalies")
    an.add_argument("--lookback-days", type=int, default=30)
    an.add_argument("--sensitivity", type=float, default=2.5)
    an.add_argument("--group-by", default="service", choices=["day", "service", "region", "resource"])

    un = sub.add_parser("underutilized", help="Find underutilized resources")
    un.add_argument("--cpu-threshold", type=float, default=20.0)
    un.add_argument("--lookback-days", type=int, default=30)

    sav = sub.add_parser("simulate", help="Simulate savings for an action")
    sav.add_argument(
        "action",
        choices=[
            "rightsize",
            "reserved_instances",
            "savings_plan",
            "delete_idle",
            "s3_intelligent_tiering",
            "stop_underutilized",
        ],
    )
    sav.add_argument("--lookback-days", type=int, default=30)
    sav.add_argument("--discount-pct", type=float, default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_path = args.data

    if args.command == "investigate":
        agent = FinOpsAgent(data_path=data_path)
        trace = agent.investigate(lookback_days=args.lookback_days)
        if args.json:
            payload = json.dumps(trace.to_dict(), indent=2)
        else:
            payload = agent.format_markdown(trace)
        if args.output:
            with open(args.output, "w") as f:
                f.write(payload)
            print(f"Wrote {args.output}", file=sys.stderr)
        print(payload)
        return 0

    if args.command == "investigate-mcp":
        if data_path is not None:
            print(
                "Note: --data is ignored for investigate-mcp; "
                "the MCP server uses its bundled sample dataset.",
                file=sys.stderr,
            )
        agent = MCPFinOpsAgent()
        trace = asyncio.run(agent.investigate(lookback_days=args.lookback_days))
        if args.json:
            payload = json.dumps(trace.to_dict(), indent=2)
        else:
            payload = agent.format_markdown(trace)
        if args.output:
            with open(args.output, "w") as f:
                f.write(payload)
            print(f"Wrote {args.output}", file=sys.stderr)
        print(payload)
        return 0

    if args.command == "breakdown":
        result = get_cost_breakdown(
            group_by=args.group_by,
            service=args.service,
            region=args.region,
            start_date=args.start_date,
            end_date=args.end_date,
            data_path=data_path,
        )
        print(result.model_dump_json(indent=2))
        return 0

    if args.command == "anomalies":
        result = detect_anomaly(
            lookback_days=args.lookback_days,
            sensitivity=args.sensitivity,
            group_by=args.group_by,
            data_path=data_path,
        )
        print(result.model_dump_json(indent=2))
        return 0

    if args.command == "underutilized":
        result = find_underutilized_resources(
            cpu_threshold_pct=args.cpu_threshold,
            lookback_days=args.lookback_days,
            data_path=data_path,
        )
        print(result.model_dump_json(indent=2))
        return 0

    if args.command == "simulate":
        result = simulate_savings(
            action=args.action,
            lookback_days=args.lookback_days,
            discount_pct=args.discount_pct,
            data_path=data_path,
        )
        print(result.model_dump_json(indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
