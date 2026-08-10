"""Planners for Version 4 — decide which MCP tools to call.

``OpenAICompatPlanner`` uses an OpenAI-compatible Chat Completions API with
tool/function calling when ``OPENAI_API_KEY`` (or a custom key) is available.

``OfflineIntentPlanner`` provides a deterministic no-key stand-in that still
drives the same multi-turn tool loop (useful for demos and CI).
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from finops_agent.mcp_client import MCPToolInfo

Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class ToolCallRequest:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ChatMessage:
    role: Role
    content: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None


@dataclass
class PlannerDecision:
    """One planner turn: either tool calls, a final answer, or both."""

    content: str | None = None
    tool_calls: list[ToolCallRequest] = field(default_factory=list)
    raw: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class Planner(Protocol):
    name: str

    async def decide(
        self,
        messages: list[ChatMessage],
        tools: list[MCPToolInfo],
    ) -> PlannerDecision:
        """Return the next assistant turn given conversation + available tools."""


SYSTEM_PROMPT = """You are a FinOps cloud cost optimization assistant.
You must use the provided MCP tools to answer questions about cloud spend.
Prefer structured tool results over guessing.
When you have enough evidence, respond with a concise final answer that cites
numbers from the tool outputs (totals, top services, anomalies, savings).
Do not invent costs that were not returned by tools.
"""


def mcp_tools_to_openai(tools: list[MCPToolInfo]) -> list[dict[str, Any]]:
    """Convert MCP tool metadata into OpenAI function/tool schemas."""
    converted: list[dict[str, Any]] = []
    for tool in tools:
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description or tool.name,
                    "parameters": tool.input_schema
                    or {"type": "object", "properties": {}},
                },
            }
        )
    return converted


def messages_to_openai(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for msg in messages:
        item: dict[str, Any] = {"role": msg.role}
        if msg.role == "assistant" and msg.tool_calls:
            item["content"] = msg.content
            item["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in msg.tool_calls
            ]
        elif msg.role == "tool":
            item["tool_call_id"] = msg.tool_call_id
            item["content"] = msg.content or ""
            if msg.name:
                item["name"] = msg.name
        else:
            item["content"] = msg.content or ""
        payload.append(item)
    return payload


class OpenAICompatPlanner:
    """LLM planner via OpenAI-compatible Chat Completions + tools."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OpenAICompatPlanner requires OPENAI_API_KEY or api_key=..."
            )
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def decide(
        self,
        messages: list[ChatMessage],
        tools: list[MCPToolInfo],
    ) -> PlannerDecision:
        body = {
            "model": self.model,
            "messages": messages_to_openai(messages),
            "tools": mcp_tools_to_openai(tools),
            "tool_choice": "auto",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self.timeout) as http:
            response = await http.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=body,
            )
            response.raise_for_status()
            data = response.json()

        choice = data["choices"][0]["message"]
        content = choice.get("content")
        tool_calls: list[ToolCallRequest] = []
        for raw in choice.get("tool_calls") or []:
            fn = raw.get("function") or {}
            args_raw = fn.get("arguments") or "{}"
            try:
                arguments = json.loads(args_raw) if isinstance(args_raw, str) else dict(args_raw)
            except json.JSONDecodeError:
                arguments = {"_raw": args_raw}
            tool_calls.append(
                ToolCallRequest(
                    id=raw.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    name=fn.get("name") or "",
                    arguments=arguments,
                )
            )
        return PlannerDecision(content=content, tool_calls=tool_calls, raw=data)


class OfflineIntentPlanner:
    """Deterministic planner for demos/CI without an API key.

    Still participates in the multi-turn tool loop:
    1) first turn → choose MCP tool calls from the user question
    2) later turn → summarize tool JSON into a final answer
    """

    name = "offline"

    def __init__(self, *, lookback_days: int = 30) -> None:
        self.lookback_days = lookback_days

    async def decide(
        self,
        messages: list[ChatMessage],
        tools: list[MCPToolInfo],
    ) -> PlannerDecision:
        available = {t.name for t in tools}
        has_tool_results = any(m.role == "tool" for m in messages)
        if has_tool_results:
            return PlannerDecision(content=self._finalize(messages))

        question = _latest_user_text(messages).lower()
        calls = self._select_tools(question, available)
        if not calls:
            # Fallback: ask for a full optimization report if available.
            if "generate_optimization_report" in available:
                calls = [
                    ToolCallRequest(
                        id=_call_id(),
                        name="generate_optimization_report",
                        arguments={"lookback_days": self.lookback_days},
                    )
                ]
        return PlannerDecision(
            content="I'll query the FinOps MCP tools for structured evidence.",
            tool_calls=calls,
        )

    def _select_tools(
        self, question: str, available: set[str]
    ) -> list[ToolCallRequest]:
        calls: list[ToolCallRequest] = []

        wants_breakdown = bool(
            re.search(r"\b(breakdown|spend\w*|cost\w*|where|money|service|region|team)\b", question)
        )
        wants_anomaly = bool(
            re.search(r"\b(anomal\w*|spike\w*|unusual|outlier\w*)\b", question)
        )
        wants_waste = bool(
            re.search(
                r"\b(underutil\w*|idle|waste|unused|rightsize|rightsizing)\b",
                question,
            )
        )
        wants_savings = bool(
            re.search(
                r"\b(saving\w*|simulate|reserved|ri\b|savings plan|tiering|delete idle)\b",
                question,
            )
        )
        wants_report = bool(
            re.search(
                r"\b(report|optimize|optimisation|optimization|summary|full|overall)\b",
                question,
            )
        )
        # Broad questions → gather several signals.
        broad = wants_report or question.strip() in {"hi", "hello"} or "what" in question

        if (wants_breakdown or broad) and "get_cost_breakdown" in available:
            group_by = "service"
            if "region" in question:
                group_by = "region"
            elif "team" in question:
                group_by = "team"
            calls.append(
                ToolCallRequest(
                    id=_call_id(),
                    name="get_cost_breakdown",
                    arguments={"group_by": group_by},
                )
            )

        if (wants_anomaly or broad) and "detect_anomaly" in available:
            calls.append(
                ToolCallRequest(
                    id=_call_id(),
                    name="detect_anomaly",
                    arguments={
                        "lookback_days": self.lookback_days,
                        "sensitivity": 2.5,
                        "group_by": "service",
                    },
                )
            )

        if (wants_waste or broad) and "find_underutilized_resources" in available:
            calls.append(
                ToolCallRequest(
                    id=_call_id(),
                    name="find_underutilized_resources",
                    arguments={
                        "cpu_threshold_pct": 20.0,
                        "lookback_days": self.lookback_days,
                    },
                )
            )

        if wants_savings and "simulate_savings" in available:
            action = "rightsize"
            if "delete" in question or "idle" in question:
                action = "delete_idle"
            elif "s3" in question or "tier" in question:
                action = "s3_intelligent_tiering"
            elif "reserved" in question or re.search(r"\bri\b", question):
                action = "reserved_instances"
            elif "savings plan" in question:
                action = "savings_plan"
            calls.append(
                ToolCallRequest(
                    id=_call_id(),
                    name="simulate_savings",
                    arguments={"action": action, "lookback_days": self.lookback_days},
                )
            )

        if wants_report and "generate_optimization_report" in available:
            calls.append(
                ToolCallRequest(
                    id=_call_id(),
                    name="generate_optimization_report",
                    arguments={"lookback_days": self.lookback_days},
                )
            )

        # Deduplicate by tool name (keep first).
        seen: set[str] = set()
        unique: list[ToolCallRequest] = []
        for call in calls:
            if call.name in seen:
                continue
            seen.add(call.name)
            unique.append(call)
        return unique

    def _finalize(self, messages: list[ChatMessage]) -> str:
        tool_payloads: list[tuple[str, Any]] = []
        for msg in messages:
            if msg.role != "tool":
                continue
            name = msg.name or "tool"
            try:
                payload = json.loads(msg.content or "{}")
            except json.JSONDecodeError:
                payload = msg.content
            tool_payloads.append((name, payload))

        lines = ["## FinOps answer (offline planner)", ""]
        for name, payload in tool_payloads:
            if not isinstance(payload, dict):
                lines.append(f"- `{name}` returned non-JSON output.")
                continue
            if name == "get_cost_breakdown":
                total = payload.get("total_cost_usd")
                top = (payload.get("buckets") or [])[:3]
                top_txt = ", ".join(
                    f"{b.get('key')} (${b.get('cost_usd')})" for b in top
                )
                lines.append(f"- **Spend:** ${total} total. Top: {top_txt}.")
            elif name == "detect_anomaly":
                lines.append(
                    f"- **Anomalies:** {payload.get('summary') or len(payload.get('anomalies') or [])}"
                )
            elif name == "find_underutilized_resources":
                lines.append(
                    f"- **Waste:** {payload.get('summary')}"
                )
            elif name == "simulate_savings":
                lines.append(
                    f"- **Savings sim ({payload.get('action')}):** "
                    f"${payload.get('total_monthly_savings_usd')}/mo "
                    f"(${payload.get('total_annual_savings_usd')}/yr)."
                )
            elif name == "generate_optimization_report":
                lines.append(f"- **Report:** {payload.get('narrative')}")
                recs = payload.get("recommendations") or []
                for rec in recs[:4]:
                    lines.append(
                        f"  - {rec.get('priority')} [{rec.get('category')}] "
                        f"{rec.get('title')} (~${rec.get('estimated_monthly_savings_usd')}/mo)"
                    )
            else:
                lines.append(f"- `{name}` keys: {sorted(payload.keys())[:8]}")
        if len(lines) == 2:
            lines.append("No structured tool results were available to summarize.")
        return "\n".join(lines)


class ScriptedPlanner:
    """Test helper: return predetermined decisions in order."""

    name = "scripted"

    def __init__(self, decisions: list[PlannerDecision]) -> None:
        self._decisions = list(decisions)
        self._idx = 0

    async def decide(
        self,
        messages: list[ChatMessage],
        tools: list[MCPToolInfo],
    ) -> PlannerDecision:
        if self._idx >= len(self._decisions):
            return PlannerDecision(content="(scripted planner exhausted)")
        decision = self._decisions[self._idx]
        self._idx += 1
        return decision


def resolve_planner(
    kind: str | None = None,
    *,
    model: str = "gpt-4o-mini",
    lookback_days: int = 30,
    api_key: str | None = None,
    base_url: str = "https://api.openai.com/v1",
) -> Planner:
    """Pick a planner. Default: openai when key present, else offline."""
    selected = (kind or "").strip().lower() or None
    if selected in {None, "auto"}:
        key = api_key or os.environ.get("OPENAI_API_KEY")
        selected = "openai" if key else "offline"
    if selected == "openai":
        return OpenAICompatPlanner(api_key=api_key, model=model, base_url=base_url)
    if selected == "offline":
        return OfflineIntentPlanner(lookback_days=lookback_days)
    raise ValueError(f"Unknown planner {kind!r}. Use auto|openai|offline.")


def _latest_user_text(messages: list[ChatMessage]) -> str:
    for msg in reversed(messages):
        if msg.role == "user" and msg.content:
            return msg.content
    return ""


def _call_id() -> str:
    return f"call_{uuid.uuid4().hex[:10]}"
