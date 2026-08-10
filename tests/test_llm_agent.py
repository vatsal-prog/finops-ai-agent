"""Tests for Version 4 LLM/offline planner agent over MCP."""

from __future__ import annotations

import asyncio
import inspect
import json

import pytest

from finops_agent.llm_agent import LLMFinOpsAgent
from finops_agent.mcp_client import FinOpsMCPClient
from finops_agent.planners import (
    OfflineIntentPlanner,
    OpenAICompatPlanner,
    PlannerDecision,
    ScriptedPlanner,
    ToolCallRequest,
    mcp_tools_to_openai,
    resolve_planner,
)


def test_v4_modules_do_not_import_analytics():
    import finops_agent.llm_agent as llm_mod
    import finops_agent.planners as plan_mod

    for mod in (llm_mod, plan_mod):
        source = inspect.getsource(mod)
        assert "from finops_agent.analytics" not in source
        assert "import finops_agent.analytics" not in source
        assert "analytics" not in mod.__dict__


def test_resolve_planner_defaults_to_offline_without_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    planner = resolve_planner("auto")
    assert isinstance(planner, OfflineIntentPlanner)


def test_resolve_planner_openai_requires_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        resolve_planner("openai")


def test_mcp_tools_to_openai_schema():
    from finops_agent.mcp_client import MCPToolInfo

    tools = [
        MCPToolInfo(
            name="get_cost_breakdown",
            description="Break down costs",
            input_schema={
                "type": "object",
                "properties": {"group_by": {"type": "string"}},
            },
        )
    ]
    converted = mcp_tools_to_openai(tools)
    assert converted[0]["type"] == "function"
    assert converted[0]["function"]["name"] == "get_cost_breakdown"
    assert "group_by" in converted[0]["function"]["parameters"]["properties"]


def test_offline_planner_selects_anomaly_tools():
    async def _run():
        planner = OfflineIntentPlanner()
        from finops_agent.mcp_client import MCPToolInfo

        tools = [
            MCPToolInfo(name="detect_anomaly", description="", input_schema={}),
            MCPToolInfo(name="get_cost_breakdown", description="", input_schema={}),
        ]
        from finops_agent.planners import ChatMessage

        decision = await planner.decide(
            [ChatMessage(role="user", content="Any unusual spending anomalies?")],
            tools,
        )
        names = {c.name for c in decision.tool_calls}
        assert "detect_anomaly" in names
        return decision

    decision = asyncio.run(_run())
    assert decision.has_tool_calls


def test_llm_agent_ask_offline_via_mcp():
    async def _run():
        agent = LLMFinOpsAgent(planner_kind="offline")
        return await agent.ask(
            "Where is our cloud money going by service, and are there anomalies?"
        )

    trace = asyncio.run(_run())
    assert trace.planner == "offline"
    assert "get_cost_breakdown" in trace.discovered_tools
    assert trace.tool_steps
    names = {s.name for s in trace.tool_steps}
    assert "get_cost_breakdown" in names
    assert "detect_anomaly" in names
    assert trace.answer
    assert "Spend" in trace.answer or "$" in trace.answer
    assert isinstance(trace.tool_steps[0].result, dict)
    md = LLMFinOpsAgent(planner_kind="offline").format_markdown(trace)
    assert "FinOps LLM Agent Answer (V4)" in md
    assert "Final answer" in md


def test_llm_agent_with_scripted_planner():
    async def _run():
        decisions = [
            PlannerDecision(
                content="calling tools",
                tool_calls=[
                    ToolCallRequest(
                        id="call_1",
                        name="get_cost_breakdown",
                        arguments={"group_by": "service"},
                    )
                ],
            ),
            PlannerDecision(content="Total spend looks high in S3."),
        ]
        async with FinOpsMCPClient() as client:
            agent = LLMFinOpsAgent(planner=ScriptedPlanner(decisions), client=client)
            return await agent.ask("Break down costs")

    trace = asyncio.run(_run())
    assert len(trace.tool_steps) == 1
    assert trace.tool_steps[0].name == "get_cost_breakdown"
    assert trace.tool_steps[0].result["total_cost_usd"] > 0
    assert trace.answer == "Total spend looks high in S3."
    assert trace.turns >= 2


def test_openai_planner_parses_tool_calls(monkeypatch):
    """Unit-test OpenAI response parsing without a live API."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "detect_anomaly",
                                        "arguments": json.dumps(
                                            {"lookback_days": 30, "group_by": "service"}
                                        ),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, headers=None, json=None):
            assert url.endswith("/chat/completions")
            assert "Authorization" in headers
            return FakeResponse()

    monkeypatch.setattr("finops_agent.planners.httpx.AsyncClient", FakeClient)

    async def _run():
        planner = OpenAICompatPlanner(api_key="sk-test", model="gpt-4o-mini")
        from finops_agent.mcp_client import MCPToolInfo
        from finops_agent.planners import ChatMessage

        return await planner.decide(
            [ChatMessage(role="user", content="find anomalies")],
            [MCPToolInfo(name="detect_anomaly", description="x", input_schema={})],
        )

    decision = asyncio.run(_run())
    assert decision.has_tool_calls
    assert decision.tool_calls[0].name == "detect_anomaly"
    assert decision.tool_calls[0].arguments["lookback_days"] == 30
