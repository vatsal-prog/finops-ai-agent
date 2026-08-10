"""Version 4 — LLM (or offline) planner agent over MCP tools.

Flow:
  user question → planner decides tool calls → FinOpsMCPClient.call_tool
  → planner sees JSON results → final natural-language answer

Does **not** import analytics. Keeps V1/V2/V3 modules unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from finops_agent.mcp_client import FinOpsMCPClient, MCPToolInfo
from finops_agent.planners import (
    SYSTEM_PROMPT,
    ChatMessage,
    OfflineIntentPlanner,
    Planner,
    PlannerDecision,
    ToolCallRequest,
    resolve_planner,
)


@dataclass
class LLMToolStep:
    name: str
    arguments: dict[str, Any]
    result: Any
    tool_call_id: str


@dataclass
class LLMAgentTrace:
    question: str
    planner: str
    discovered_tools: list[str] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    tool_steps: list[LLMToolStep] = field(default_factory=list)
    answer: str | None = None
    turns: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "planner": self.planner,
            "discovered_tools": list(self.discovered_tools),
            "turns": self.turns,
            "tool_steps": [
                {
                    "tool_call_id": s.tool_call_id,
                    "name": s.name,
                    "arguments": s.arguments,
                    "result": s.result,
                }
                for s in self.tool_steps
            ],
            "answer": self.answer,
            "messages": list(self.messages),
        }


class LLMFinOpsAgent:
    """Multi-turn FinOps agent: planner chooses MCP tools dynamically."""

    def __init__(
        self,
        *,
        planner: Planner | None = None,
        client: FinOpsMCPClient | None = None,
        max_turns: int = 6,
        planner_kind: str | None = None,
        model: str = "gpt-4o-mini",
        lookback_days: int = 30,
    ) -> None:
        self.planner = planner or resolve_planner(
            planner_kind, model=model, lookback_days=lookback_days
        )
        self._client = client
        self.max_turns = max_turns
        self.lookback_days = lookback_days

    async def ask(self, question: str) -> LLMAgentTrace:
        """Answer a natural-language FinOps question using MCP tools."""
        trace = LLMAgentTrace(question=question, planner=getattr(self.planner, "name", "planner"))
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(role="user", content=question),
        ]

        if self._client is not None:
            client = self._client
            if not client.connected:
                await client.connect()
            await self._run_loop(client, messages, trace)
            return trace

        async with FinOpsMCPClient() as client:
            await self._run_loop(client, messages, trace)
            return trace

    async def _run_loop(
        self,
        client: FinOpsMCPClient,
        messages: list[ChatMessage],
        trace: LLMAgentTrace,
    ) -> None:
        tools = await client.list_tools()
        trace.discovered_tools = [t.name for t in tools]
        available = {t.name for t in tools}

        for turn in range(1, self.max_turns + 1):
            trace.turns = turn
            decision = await self.planner.decide(messages, tools)
            messages.append(
                ChatMessage(
                    role="assistant",
                    content=decision.content,
                    tool_calls=list(decision.tool_calls),
                )
            )
            trace.messages.append(_message_dict(messages[-1]))

            if not decision.has_tool_calls:
                trace.answer = (decision.content or "").strip() or None
                return

            for call in decision.tool_calls:
                if call.name not in available:
                    result: Any = {
                        "error": f"Unknown tool {call.name!r}",
                        "available": sorted(available),
                    }
                else:
                    result = await client.call_tool(call.name, call.arguments)
                step = LLMToolStep(
                    name=call.name,
                    arguments=call.arguments,
                    result=result,
                    tool_call_id=call.id,
                )
                trace.tool_steps.append(step)
                tool_msg = ChatMessage(
                    role="tool",
                    name=call.name,
                    tool_call_id=call.id,
                    content=json.dumps(result, default=str),
                )
                messages.append(tool_msg)
                trace.messages.append(_message_dict(tool_msg))

        # Max turns reached — ask planner for a best-effort final answer with no new tools.
        if not isinstance(self.planner, OfflineIntentPlanner):
            # Nudge with a user message; offline planner already finalizes on tool results.
            messages.append(
                ChatMessage(
                    role="user",
                    content=(
                        "Stop calling tools. Provide the best final answer from the "
                        "tool results you already have."
                    ),
                )
            )
            decision = await self.planner.decide(messages, tools=[])
            messages.append(
                ChatMessage(role="assistant", content=decision.content, tool_calls=[])
            )
            trace.messages.append(_message_dict(messages[-1]))
            trace.answer = (decision.content or "").strip() or None
        elif trace.tool_steps and not trace.answer:
            # Shouldn't happen for offline (it finalizes after tools), but keep safe.
            decision = await self.planner.decide(messages, tools)
            trace.answer = (decision.content or "").strip() or None

    def format_markdown(self, trace: LLMAgentTrace) -> str:
        lines = [
            "# FinOps LLM Agent Answer (V4)",
            "",
            f"**Question:** {trace.question}",
            f"**Planner:** `{trace.planner}`",
            f"**Turns:** {trace.turns}",
            f"**Discovered tools:** {', '.join(f'`{t}`' for t in trace.discovered_tools)}",
            "",
            "## Tool calls",
            "",
        ]
        if not trace.tool_steps:
            lines.append("_No tools were called._")
            lines.append("")
        for i, step in enumerate(trace.tool_steps, start=1):
            lines.append(f"### {i}. `{step.name}`")
            lines.append(f"- Args: `{json.dumps(step.arguments)}`")
            preview = json.dumps(step.result, default=str)
            if len(preview) > 500:
                preview = preview[:500] + "…"
            lines.append(f"- Result preview: `{preview}`")
            lines.append("")
        lines.extend(["## Final answer", "", trace.answer or "_(no answer)_", ""])
        return "\n".join(lines)


def _message_dict(message: ChatMessage) -> dict[str, Any]:
    data: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        data["tool_calls"] = [
            {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
            for tc in message.tool_calls
        ]
    if message.tool_call_id:
        data["tool_call_id"] = message.tool_call_id
    if message.name:
        data["name"] = message.name
    return data
