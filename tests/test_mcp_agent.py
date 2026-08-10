"""Tests for Version 3 MCP-powered FinOps agent."""

from __future__ import annotations

import asyncio
import inspect

import pytest

from finops_agent.mcp_agent import MCPFinOpsAgent, PLAYBOOK, summarize_mcp_result
from finops_agent.mcp_client import FinOpsMCPClient


def test_mcp_agent_module_does_not_import_analytics():
    import finops_agent.mcp_agent as mod

    source = inspect.getsource(mod)
    assert "from finops_agent.analytics" not in source
    assert "import finops_agent.analytics" not in source
    assert "analytics" not in mod.__dict__
    assert "FinOpsAgent" not in mod.__dict__


def test_playbook_covers_core_tools():
    names = {name for name, _ in PLAYBOOK}
    assert {
        "get_cost_breakdown",
        "detect_anomaly",
        "find_underutilized_resources",
        "simulate_savings",
        "generate_optimization_report",
    } <= names


def test_summarize_mcp_result_cost_breakdown():
    summary = summarize_mcp_result(
        "get_cost_breakdown",
        {
            "total_cost_usd": 100.0,
            "period_over_period_pct": 1.5,
            "buckets": [{"key": "S3", "cost_usd": 60.0, "share_pct": 60.0}],
        },
    )
    assert summary["total_cost_usd"] == 100.0
    assert summary["top_buckets"][0]["key"] == "S3"


def test_mcp_agent_investigate_via_mcp():
    async def _run():
        agent = MCPFinOpsAgent()
        return await agent.investigate(lookback_days=30)

    trace = asyncio.run(_run())
    assert trace.transport == "mcp-stdio"
    assert "get_cost_breakdown" in trace.discovered_tools
    assert len(trace.steps) == len(PLAYBOOK)
    assert [s.name for s in trace.steps] == [name for name, _ in PLAYBOOK]
    assert trace.report is not None
    assert trace.report["total_cost_usd"] > 0
    assert trace.report["recommendations"]
    assert isinstance(trace.steps[0].result, dict)

    md = MCPFinOpsAgent().format_markdown(trace)
    assert "FinOps MCP Agent Investigation Report (V3)" in md
    assert "Discovered tools" in md
    assert "Executive Summary" in md

    payload = trace.to_dict()
    assert payload["steps"][0]["result_summary"]["total_cost_usd"] > 0


def test_mcp_agent_reuses_injected_client():
    async def _run():
        async with FinOpsMCPClient() as client:
            tools_before = await client.list_tools()
            agent = MCPFinOpsAgent(client=client)
            trace = await agent.investigate(lookback_days=30)
            assert client.connected
            return trace, [t.name for t in tools_before]

    trace, tool_names = asyncio.run(_run())
    assert set(tool_names) == set(trace.discovered_tools)
    assert trace.report is not None


def test_mcp_agent_fails_if_required_tool_missing(monkeypatch):
    class FakeClient:
        connected = True

        async def connect(self):
            return self

        async def list_tools(self):
            from finops_agent.mcp_client import MCPToolInfo

            return [
                MCPToolInfo(name="get_cost_breakdown", description="x", input_schema={})
            ]

        async def call_tool(self, name, arguments=None, parse_json=True):
            raise AssertionError("should not call tools when discovery fails")

    async def _run():
        agent = MCPFinOpsAgent(client=FakeClient())  # type: ignore[arg-type]
        await agent.investigate(lookback_days=30)

    with pytest.raises(RuntimeError, match="missing required tools"):
        asyncio.run(_run())
