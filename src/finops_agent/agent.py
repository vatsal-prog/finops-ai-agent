"""Rule-based FinOps agent that orchestrates structured MCP-equivalent tools.

Demonstrates an agent loop working over structured billing/utilization data
without requiring an external LLM — useful for demos, CI, and offline runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from finops_agent.analytics import (
    build_optimization_report,
    detect_anomaly,
    find_underutilized_resources,
    get_cost_breakdown,
    simulate_savings,
)
from finops_agent.models import OptimizationReport


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result: Any


@dataclass
class AgentTrace:
    goal: str
    steps: list[ToolCall] = field(default_factory=list)
    report: OptimizationReport | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": [
                {
                    "tool": s.name,
                    "arguments": s.arguments,
                    "result_summary": _summarize(s.name, s.result),
                }
                for s in self.steps
            ],
            "report": self.report.model_dump() if self.report else None,
        }


def _summarize(name: str, result: Any) -> dict[str, Any]:
    if name == "get_cost_breakdown":
        return {
            "total_cost_usd": result.total_cost_usd,
            "top_buckets": [
                {"key": b.key, "cost_usd": b.cost_usd, "share_pct": b.share_pct}
                for b in result.buckets[:5]
            ],
            "period_over_period_pct": result.period_over_period_pct,
        }
    if name == "detect_anomaly":
        return {
            "anomaly_count": len(result.anomalies),
            "top": [
                {
                    "date": a.date,
                    "service": a.service,
                    "z_score": a.z_score,
                    "severity": a.severity,
                    "actual_cost_usd": a.actual_cost_usd,
                }
                for a in result.anomalies[:5]
            ],
            "summary": result.summary,
        }
    if name == "find_underutilized_resources":
        return {
            "count": len(result.resources),
            "total_monthly_savings_usd": result.total_monthly_savings_usd,
            "top": [
                {
                    "resource_id": r.resource_id,
                    "service": r.service,
                    "avg_cpu_pct": r.avg_cpu_pct,
                    "estimated_monthly_savings_usd": r.estimated_monthly_savings_usd,
                    "recommendation": r.recommendation,
                }
                for r in result.resources[:5]
            ],
            "summary": result.summary,
        }
    if name == "simulate_savings":
        return {
            "action": result.action,
            "monthly_savings_usd": result.total_monthly_savings_usd,
            "annual_savings_usd": result.total_annual_savings_usd,
            "summary": result.summary,
        }
    if name == "generate_optimization_report":
        return {
            "total_cost_usd": result.total_cost_usd,
            "anomaly_count": result.anomaly_count,
            "underutilized_count": result.underutilized_count,
            "potential_monthly_savings_usd": result.potential_monthly_savings_usd,
            "recommendation_count": len(result.recommendations),
        }
    return {"type": type(result).__name__}


TOOL_REGISTRY: dict[str, Callable[..., Any]] = {
    "get_cost_breakdown": get_cost_breakdown,
    "detect_anomaly": detect_anomaly,
    "find_underutilized_resources": find_underutilized_resources,
    "simulate_savings": simulate_savings,
    "generate_optimization_report": build_optimization_report,
}


class FinOpsAgent:
    """Deterministic FinOps agent that calls tools in a fixed investigative plan."""

    def __init__(self, data_path: str | None = None) -> None:
        self.data_path = data_path

    def _call(self, name: str, **kwargs: Any) -> ToolCall:
        fn = TOOL_REGISTRY[name]
        if self.data_path is not None and "data_path" not in kwargs:
            kwargs["data_path"] = self.data_path
        result = fn(**kwargs)
        return ToolCall(name=name, arguments={k: v for k, v in kwargs.items() if k != "data_path"}, result=result)

    def investigate(self, lookback_days: int = 30) -> AgentTrace:
        """Run the standard FinOps investigation playbook."""
        goal = (
            "Analyze cloud spend, detect anomalies, find underutilized resources, "
            "and recommend optimizations with quantified savings."
        )
        trace = AgentTrace(goal=goal)

        # 1. Understand where money goes
        trace.steps.append(
            self._call("get_cost_breakdown", group_by="service")
        )
        trace.steps.append(
            self._call("get_cost_breakdown", group_by="region")
        )
        trace.steps.append(
            self._call("get_cost_breakdown", group_by="team")
        )

        # 2. Detect unusual spending
        trace.steps.append(
            self._call(
                "detect_anomaly",
                lookback_days=lookback_days,
                sensitivity=2.5,
                group_by="service",
            )
        )
        trace.steps.append(
            self._call(
                "detect_anomaly",
                lookback_days=lookback_days,
                sensitivity=2.5,
                group_by="resource",
            )
        )

        # 3. Find waste
        under_step = self._call(
            "find_underutilized_resources",
            cpu_threshold_pct=20.0,
            lookback_days=lookback_days,
        )
        trace.steps.append(under_step)

        # 4. Simulate savings for top opportunity classes
        for action in ("delete_idle", "rightsize", "s3_intelligent_tiering", "reserved_instances"):
            trace.steps.append(
                self._call("simulate_savings", action=action, lookback_days=lookback_days)
            )

        # 5. Executive report
        report_step = self._call(
            "generate_optimization_report", lookback_days=lookback_days
        )
        trace.steps.append(report_step)
        trace.report = report_step.result
        return trace

    def format_markdown(self, trace: AgentTrace) -> str:
        """Render a human-readable investigation report."""
        lines: list[str] = [
            "# FinOps Agent Investigation Report",
            "",
            f"**Goal:** {trace.goal}",
            "",
            "## Tool Trace",
            "",
        ]
        for i, step in enumerate(trace.steps, start=1):
            summary = _summarize(step.name, step.result)
            lines.append(f"### {i}. `{step.name}`")
            lines.append(f"- Args: `{json.dumps(step.arguments)}`")
            lines.append(f"- Summary: `{json.dumps(summary)}`")
            lines.append("")

        if trace.report:
            r = trace.report
            lines.extend(
                [
                    "## Executive Summary",
                    "",
                    r.narrative,
                    "",
                    f"- **Total spend:** ${r.total_cost_usd:,.2f}",
                    f"- **Anomalies:** {r.anomaly_count}",
                    f"- **Underutilized resources:** {r.underutilized_count}",
                    f"- **Modeled monthly savings:** ${r.potential_monthly_savings_usd:,.2f}",
                    "",
                    "## Prioritized Recommendations",
                    "",
                ]
            )
            for rec in r.recommendations:
                lines.append(
                    f"- **{rec.priority}** [{rec.category}] {rec.title} "
                    f"(~${rec.estimated_monthly_savings_usd:,.2f}/mo)"
                )
                lines.append(f"  - {rec.detail}")
            lines.append("")
        return "\n".join(lines)
