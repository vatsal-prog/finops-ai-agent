"""FinOps MCP server — exposes structured cloud cost tools to agents."""

from __future__ import annotations

import json
from typing import Any, Literal

from mcp.server.mcpserver import MCPServer

from finops_agent.analytics import (
    build_optimization_report,
    detect_anomaly,
    find_underutilized_resources,
    get_cost_breakdown,
    simulate_savings,
)
from finops_agent.models import GroupBy, SavingsAction

server = MCPServer(
    name="finops-agent",
    version="0.1.0",
    instructions=(
        "You are connected to the FinOps Cloud Cost Optimization MCP server. "
        "Use get_cost_breakdown to analyze spend, detect_anomaly to find unusual spikes, "
        "find_underutilized_resources to locate waste, simulate_savings for what-if modeling, "
        "and generate_optimization_report for an executive summary. "
        "All tools return structured JSON suitable for agent reasoning."
    ),
)


def _dump(model: Any) -> str:
    if hasattr(model, "model_dump"):
        return json.dumps(model.model_dump(), indent=2)
    return json.dumps(model, indent=2)


@server.tool(
    name="get_cost_breakdown",
    title="Get Cost Breakdown",
    description=(
        "Aggregate cloud billing data into a structured cost breakdown. "
        "Group by service, region, resource, team, env, or day. "
        "Optionally filter by service/region/team/env and date range. "
        "Returns totals, share percentages, and period-over-period trends."
    ),
)
def get_cost_breakdown_tool(
    group_by: GroupBy = "service",
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    region: str | None = None,
    team: str | None = None,
    env: str | None = None,
) -> str:
    result = get_cost_breakdown(
        group_by=group_by,
        start_date=start_date,
        end_date=end_date,
        service=service,
        region=region,
        team=team,
        env=env,
    )
    return _dump(result)


@server.tool(
    name="detect_anomaly",
    title="Detect Cost Anomalies",
    description=(
        "Detect unusual cloud spending using z-score analysis against a rolling baseline. "
        "Set sensitivity (typical 2.0–3.0; lower = more alerts) and group_by "
        "(day, service, region, or resource). Returns ranked anomaly events with severity."
    ),
)
def detect_anomaly_tool(
    lookback_days: int = 30,
    sensitivity: float = 2.5,
    group_by: Literal["day", "service", "region", "resource"] = "service",
    service: str | None = None,
    region: str | None = None,
    end_date: str | None = None,
) -> str:
    result = detect_anomaly(
        lookback_days=lookback_days,
        sensitivity=sensitivity,
        group_by=group_by,
        service=service,
        region=region,
        end_date=end_date,
    )
    return _dump(result)


@server.tool(
    name="find_underutilized_resources",
    title="Find Underutilized Resources",
    description=(
        "Find underutilized or idle cloud resources (low CPU/memory EC2/RDS/ElastiCache, "
        "unattached EBS, idle ELBs). Returns recommendations and estimated monthly savings."
    ),
)
def find_underutilized_resources_tool(
    cpu_threshold_pct: float = 20.0,
    lookback_days: int = 30,
    end_date: str | None = None,
) -> str:
    result = find_underutilized_resources(
        cpu_threshold_pct=cpu_threshold_pct,
        lookback_days=lookback_days,
        end_date=end_date,
    )
    return _dump(result)


@server.tool(
    name="simulate_savings",
    title="Simulate Savings",
    description=(
        "What-if savings simulation for FinOps actions: rightsize, reserved_instances, "
        "savings_plan, delete_idle, s3_intelligent_tiering, or stop_underutilized. "
        "Optionally scope to specific resource_ids and override discount_pct. "
        "Returns current vs projected monthly/annual cost and assumptions/risks."
    ),
)
def simulate_savings_tool(
    action: SavingsAction,
    resource_ids: list[str] | None = None,
    lookback_days: int = 30,
    discount_pct: float | None = None,
    end_date: str | None = None,
) -> str:
    result = simulate_savings(
        action=action,
        resource_ids=resource_ids,
        lookback_days=lookback_days,
        discount_pct=discount_pct,
        end_date=end_date,
    )
    return _dump(result)


@server.tool(
    name="generate_optimization_report",
    title="Generate Optimization Report",
    description=(
        "Run a full FinOps pass: cost breakdown, anomaly detection, underutilization scan, "
        "and savings simulations. Returns a prioritized recommendation list and narrative."
    ),
)
def generate_optimization_report_tool(lookback_days: int = 30) -> str:
    result = build_optimization_report(lookback_days=lookback_days)
    return _dump(result)


def main() -> None:
    """Run the FinOps MCP server over stdio (default for Cursor / Claude Desktop)."""
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
