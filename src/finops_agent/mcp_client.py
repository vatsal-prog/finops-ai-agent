"""Version 2 — MCP client for the existing finops-agent MCP server.

Connects over stdio to ``python -m finops_agent.mcp_server`` and discovers /
invokes tools dynamically via the Model Context Protocol.

This layer does **not** import analytics functions. Version 1
(``FinOpsAgent`` in ``agent.py``) remains the direct-Python baseline.
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, get_default_environment, stdio_client

# Repo root: src/finops_agent/mcp_client.py -> parents[2] == project root
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_SRC = _PROJECT_ROOT / "src"


@dataclass(frozen=True)
class MCPToolInfo:
    """Discovered MCP tool metadata."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


class FinOpsMCPClient:
    """Async MCP client that talks to the finops-agent stdio server.

    Typical usage::

        async with FinOpsMCPClient() as client:
            tools = await client.list_tools()
            result = await client.call_tool("get_cost_breakdown", {"group_by": "service"})
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | Path | None = None,
        env: dict[str, str] | None = None,
        pythonpath: str | Path | None = None,
    ) -> None:
        self._command = command or sys.executable
        self._args = args or ["-m", "finops_agent.mcp_server"]
        self._cwd = Path(cwd) if cwd is not None else _PROJECT_ROOT
        self._pythonpath = Path(pythonpath) if pythonpath is not None else _DEFAULT_SRC
        self._env_override = env

        self._exit_stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None
        self._server_name: str | None = None
        self._server_version: str | None = None
        self._tools_cache: list[MCPToolInfo] | None = None

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def server_name(self) -> str | None:
        return self._server_name

    @property
    def server_version(self) -> str | None:
        return self._server_version

    def _build_env(self) -> dict[str, str]:
        env = get_default_environment()
        if self._env_override:
            env.update(self._env_override)
        # Ensure the spawned server can import finops_agent.
        existing = env.get("PYTHONPATH", "")
        src = str(self._pythonpath)
        env["PYTHONPATH"] = src if not existing else os.pathsep.join([src, existing])
        return env

    async def connect(self) -> FinOpsMCPClient:
        """Spawn the MCP server subprocess and complete the MCP handshake."""
        if self._session is not None:
            return self

        params = StdioServerParameters(
            command=self._command,
            args=list(self._args),
            env=self._build_env(),
            cwd=str(self._cwd),
        )

        self._exit_stack = AsyncExitStack()
        read_stream, write_stream = await self._exit_stack.enter_async_context(
            stdio_client(params)
        )
        session = await self._exit_stack.enter_async_context(
            ClientSession(read_stream, write_stream)
        )
        init = await session.initialize()
        self._session = session
        self._server_name = init.server_info.name if init.server_info else None
        self._server_version = init.server_info.version if init.server_info else None
        self._tools_cache = None
        return self

    async def close(self) -> None:
        """Tear down the MCP session and server subprocess."""
        stack = self._exit_stack
        self._exit_stack = None
        self._session = None
        self._tools_cache = None
        if stack is not None:
            await stack.aclose()

    async def __aenter__(self) -> FinOpsMCPClient:
        return await self.connect()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise RuntimeError(
                "FinOpsMCPClient is not connected. Use 'async with FinOpsMCPClient()' "
                "or call await client.connect() first."
            )
        return self._session

    async def list_tools(self, *, refresh: bool = False) -> list[MCPToolInfo]:
        """Discover tools advertised by the MCP server (dynamic tool discovery)."""
        if self._tools_cache is not None and not refresh:
            return list(self._tools_cache)

        session = self._require_session()
        result = await session.list_tools()
        tools = [
            MCPToolInfo(
                name=tool.name,
                description=tool.description,
                input_schema=dict(tool.input_schema or {}),
            )
            for tool in result.tools
        ]
        self._tools_cache = tools
        return list(tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        parse_json: bool = True,
    ) -> Any:
        """Call an MCP tool by name with JSON arguments.

        Args:
            name: Tool name as advertised by ``list_tools``.
            arguments: JSON-serializable argument dict.
            parse_json: When True (default), parse text content as JSON if possible.

        Returns:
            Parsed JSON (dict/list/…) when ``parse_json`` and content is JSON text;
            otherwise the raw text string, or a list of content blocks for mixed results.

        Raises:
            RuntimeError: If not connected, or the server returns an error result.
            ValueError: If ``name`` is not among discovered tools (after refresh).
        """
        session = self._require_session()
        tools = await self.list_tools()
        known = {t.name for t in tools}
        if name not in known:
            raise ValueError(
                f"Unknown MCP tool {name!r}. Available: {sorted(known)}"
            )

        result = await session.call_tool(name, arguments or {})
        if result.is_error:
            detail = _content_as_text(result.content)
            raise RuntimeError(f"MCP tool {name!r} failed: {detail}")

        return _extract_payload(result.content, parse_json=parse_json)


def _content_as_text(content: list[Any]) -> str:
    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _extract_payload(content: list[Any], *, parse_json: bool) -> Any:
    texts = [getattr(block, "text") for block in content if getattr(block, "text", None) is not None]
    if not texts:
        return [block.model_dump() if hasattr(block, "model_dump") else block for block in content]
    if len(texts) == 1:
        text = texts[0]
        if parse_json:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text
    if parse_json:
        parsed: list[Any] = []
        for text in texts:
            try:
                parsed.append(json.loads(text))
            except json.JSONDecodeError:
                parsed.append(text)
        return parsed
    return texts


async def list_finops_tools() -> list[MCPToolInfo]:
    """Convenience helper: connect, list tools, disconnect."""
    async with FinOpsMCPClient() as client:
        return await client.list_tools()


async def call_finops_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
    """Convenience helper: connect, call one tool, disconnect."""
    async with FinOpsMCPClient() as client:
        return await client.call_tool(name, arguments)
