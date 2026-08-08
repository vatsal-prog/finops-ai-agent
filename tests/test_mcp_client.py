"""Tests for Version 2 FinOps MCP client (stdio → MCP server)."""

from __future__ import annotations

import asyncio
import json

import pytest

from finops_agent.mcp_client import FinOpsMCPClient, MCPToolInfo, call_finops_tool


EXPECTED_TOOLS = {
    "get_cost_breakdown",
    "detect_anomaly",
    "find_underutilized_resources",
    "simulate_savings",
    "generate_optimization_report",
}


def test_mcp_client_connects_and_lists_tools():
    async def _run() -> list[MCPToolInfo]:
        async with FinOpsMCPClient() as client:
            assert client.connected
            assert client.server_name == "finops-agent"
            tools = await client.list_tools()
            return tools

    tools = asyncio.run(_run())
    names = {t.name for t in tools}
    assert EXPECTED_TOOLS <= names
    for tool in tools:
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.input_schema, dict)


def test_mcp_client_call_get_cost_breakdown():
    async def _run() -> dict:
        async with FinOpsMCPClient() as client:
            result = await client.call_tool(
                "get_cost_breakdown",
                {"group_by": "service"},
            )
            assert isinstance(result, dict)
            return result

    result = asyncio.run(_run())
    assert result["group_by"] == "service"
    assert result["total_cost_usd"] > 0
    assert result["buckets"]
    assert result["buckets"][0]["key"]
    assert "cost_usd" in result["buckets"][0]
    # Ensure structured JSON (not a raw string)
    json.dumps(result)


def test_mcp_client_rejects_unknown_tool():
    async def _run() -> None:
        async with FinOpsMCPClient() as client:
            with pytest.raises(ValueError, match="Unknown MCP tool"):
                await client.call_tool("not_a_real_tool", {})

    asyncio.run(_run())


def test_mcp_client_requires_connection():
    client = FinOpsMCPClient()

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="not connected"):
            await client.list_tools()

    asyncio.run(_run())


def test_call_finops_tool_helper():
    result = asyncio.run(
        call_finops_tool("get_cost_breakdown", {"group_by": "service"})
    )
    assert isinstance(result, dict)
    assert result["total_cost_usd"] > 0


def test_mcp_client_does_not_import_analytics_in_module():
    """Guardrail: mcp_client must talk MCP, not import analytics directly."""
    import inspect

    import finops_agent.mcp_client as mod

    source = inspect.getsource(mod)
    assert "from finops_agent.analytics" not in source
    assert "import finops_agent.analytics" not in source
    # Runtime dependency check: analytics must not be imported by this module.
    assert "finops_agent.analytics" not in getattr(mod, "__dict__", {})
    assert "analytics" not in mod.__dict__
