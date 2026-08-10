"""Version 3 — FinOps investigation agent that calls tools over MCP.

Uses ``FinOpsMCPClient`` (V2) to discover and invoke tools on the
``finops-agent`` MCP server. Does **not** import analytics functions.

Version 1 (``FinOpsAgent`` in ``agent.py``) remains the direct-Python baseline.
No LLM is used — the playbook is still deterministic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from finops_agent.mcp_client import FinOpsMCPClient, MCPToolInfo

# Fixed investigative playbook (same intent as V1, executed via MCP).
PLAYBOOK: list[tuple[str, dict[str, Any]]] = [
    ("get_cost_breakdown", {"group_by": "service"}),
    ("get_cost_breakdown", {"group_by": "region"}),
    ("get_cost_breakdown", {"group_by": "team"}),
    ("detect_anomaly", {"lookback_days": 30, "sensitivity": 2.5, "group_by": "service"}),
    ("detect_anomaly", {"lookback_days": 30, "sensitivity": 2.5, "group_by": "resource"}),
    ("find_underutilized_resources", {"cpu_threshold_pct": 20.0, "lookback_days": 30}),
    ("simulate_savings", {"action": "delete_idle", "lookback_days": 30}),
    ("simulate_savings", {"action": "rightsize", "lookback_days": 30}),
    ("simulate_savings", {"action": "s3_intelligent_tiering", "lookback_days": 30}),
    ("simulate_savings", {"action": "reserved_instances", "lookback_days": 30}),
    ("generate_optimization_report", {"lookback_days": 30}),
]


@dataclass
class MCPToolCall:
    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass
class MCPAgentTrace:
    goal: str
    discovered_tools: list[str] = field(default_factory=list)
    steps: list[MCPToolCall] = field(default_factory=list)
    report: dict[str, Any] | None = None
    transport: str = "mcp-stdio"

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "transport": self.transport,
            "discovered_tools": list(self.discovered_tools),
            "steps": [
                {
                    "tool": step.name,
                    "arguments": step.arguments,
                    "result_summary": summarize_mcp_result(step.name, step.result),
                }
                for step in self.steps
            ],
            "report": self.report,
        }


def summarize_mcp_result(name: str, result: Any) -> dict[str, Any]:
    """Summarize structured MCP JSON tool results for traces/reports."""
    if not isinstance(result, dict):
        return {"type": type(result).__name__}

    if name == "get_cost_breakdown":
        buckets = result.get("buckets") or []
        return {
            "total_cost_usd": result.get("total_cost_usd"),
            "top_buckets": [
                {
                    "key": b.get("key"),
                    "cost_usd": b.get("cost_usd"),
                    "share_pct": b.get("share_pct"),
                }
                for b in buckets[:5]
            ],
            "period_over_period_pct": result.get("period_over_period_pct"),
        }
    if name == "detect_anomaly":
        anomalies = result.get("anomalies") or []
        return {
            "anomaly_count": len(anomalies),
            "top": [
                {
                    "date": a.get("date"),
                    "service": a.get("service"),
                    "z_score": a.get("z_score"),
                    "severity": a.get("severity"),
                    "actual_cost_usd": a.get("actual_cost_usd"),
                }
                for a in anomalies[:5]
            ],
            "summary": result.get("summary"),
        }
    if name == "find_underutilized_resources":
        resources = result.get("resources") or []
        return {
            "count": len(resources),
            "total_monthly_savings_usd": result.get("total_monthly_savings_usd"),
            "top": [
                {
                    "resource_id": r.get("resource_id"),
                    "service": r.get("service"),
                    "avg_cpu_pct": r.get("avg_cpu_pct"),
                    "estimated_monthly_savings_usd": r.get("estimated_monthly_savings_usd"),
                    "recommendation": r.get("recommendation"),
                }
                for r in resources[:5]
            ],
            "summary": result.get("summary"),
        }
    if name == "simulate_savings":
        return {
            "action": result.get("action"),
            "monthly_savings_usd": result.get("total_monthly_savings_usd"),
            "annual_savings_usd": result.get("total_annual_savings_usd"),
            "summary": result.get("summary"),
        }
    if name == "generate_optimization_report":
        recs = result.get("recommendations") or []
        return {
            "total_cost_usd": result.get("total_cost_usd"),
            "anomaly_count": result.get("anomaly_count"),
            "underutilized_count": result.get("underutilized_count"),
            "potential_monthly_savings_usd": result.get("potential_monthly_savings_usd"),
            "recommendation_count": len(recs),
        }
    return {"keys": sorted(result.keys())}


def _with_lookback(arguments: dict[str, Any], lookback_days: int) -> dict[str, Any]:
    args = dict(arguments)
    if "lookback_days" in args:
        args["lookback_days"] = lookback_days
    return args


class MCPFinOpsAgent:
    """Deterministic FinOps agent that executes the investigation playbook over MCP."""

    def __init__(self, client: FinOpsMCPClient | None = None) -> None:
        self._client = client
        self._owns_client = client is None

    async def investigate(self, lookback_days: int = 30) -> MCPAgentTrace:
        """Discover MCP tools, then run the fixed FinOps playbook via call_tool."""
        goal = (
            "Analyze cloud spend, detect anomalies, find underutilized resources, "
            "and recommend optimizations with quantified savings (via MCP)."
        )
        trace = MCPAgentTrace(goal=goal)

        if self._client is not None:
            client = self._client
            if not client.connected:
                await client.connect()
            tools = await client.list_tools()
            await self._run_playbook(client, trace, tools, lookback_days)
            return trace

        async with FinOpsMCPClient() as client:
            tools = await client.list_tools()
            await self._run_playbook(client, trace, tools, lookback_days)
            return trace

    async def _run_playbook(
        self,
        client: FinOpsMCPClient,
        trace: MCPAgentTrace,
        tools: list[MCPToolInfo],
        lookback_days: int,
    ) -> None:
        discovered = [t.name for t in tools]
        trace.discovered_tools = discovered
        available = set(discovered)

        required = {name for name, _ in PLAYBOOK}
        missing = sorted(required - available)
        if missing:
            raise RuntimeError(
                f"MCP server is missing required tools: {missing}. "
                f"Discovered: {discovered}"
            )

        for name, raw_args in PLAYBOOK:
            arguments = _with_lookback(raw_args, lookback_days)
            result = await client.call_tool(name, arguments)
            trace.steps.append(MCPToolCall(name=name, arguments=arguments, result=result))
            if name == "generate_optimization_report" and isinstance(result, dict):
                trace.report = result

    def format_markdown(self, trace: MCPAgentTrace) -> str:
        """Render a human-readable investigation report from MCP tool results."""
        lines: list[str] = [
            "# FinOps MCP Agent Investigation Report (V3)",
            "",
            f"**Goal:** {trace.goal}",
            f"**Transport:** `{trace.transport}`",
            f"**Discovered tools:** {', '.join(f'`{t}`' for t in trace.discovered_tools)}",
            "",
            "## Tool Trace",
            "",
        ]
        for i, step in enumerate(trace.steps, start=1):
            summary = summarize_mcp_result(step.name, step.result)
            lines.append(f"### {i}. `{step.name}`")
            lines.append(f"- Args: `{json.dumps(step.arguments)}`")
            lines.append(f"- Summary: `{json.dumps(summary)}`")
            lines.append("")

        report = trace.report
        if report:
            lines.extend(
                [
                    "## Executive Summary",
                    "",
                    str(report.get("narrative") or ""),
                    "",
                    f"- **Total spend:** ${_money(report.get('total_cost_usd'))}",
                    f"- **Anomalies:** {report.get('anomaly_count')}",
                    f"- **Underutilized resources:** {report.get('underutilized_count')}",
                    f"- **Modeled monthly savings:** ${_money(report.get('potential_monthly_savings_usd'))}",
                    "",
                    "## Prioritized Recommendations",
                    "",
                ]
            )
            for rec in report.get("recommendations") or []:
                lines.append(
                    f"- **{rec.get('priority')}** [{rec.get('category')}] {rec.get('title')} "
                    f"(~${_money(rec.get('estimated_monthly_savings_usd'))}/mo)"
                )
                if rec.get("detail"):
                    lines.append(f"  - {rec['detail']}")
            lines.append("")
        return "\n".join(lines)


def _money(value: Any) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "0.00"
