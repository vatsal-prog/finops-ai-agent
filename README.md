# FinOps Agent — Cloud Cost Optimization

An AI-ready **FinOps agent** that analyzes cloud usage and billing data, detects unusual spending, finds underutilized resources, and recommends optimizations.

Built as a demonstration of **agents working with structured data** via custom **MCP (Model Context Protocol)** tools:

| MCP Tool | Purpose |
|---|---|
| `get_cost_breakdown()` | Aggregate spend by service, region, resource, team, env, or day |
| `detect_anomaly()` | Z-score anomaly detection for unusual spending spikes |
| `find_underutilized_resources()` | Surface low-CPU / idle / unattached waste |
| `simulate_savings()` | What-if modeling (rightsize, RIs, Savings Plans, delete idle, S3 tiering) |
| `generate_optimization_report()` | Full prioritized FinOps report + narrative |

```
┌─────────────┐   HTTP    ┌──────────────────┐  MCP stdio  ┌─────────────────────┐
│ V5 Dashboard│ ────────► │ /api/ask (V4)    │ ──────────► │ finops MCP server   │
│ (browser)   │           │ /api/overview    │             │ + analytics + data  │
└─────────────┘           └──────────────────┘             └─────────────────────┘
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# V1 — direct Python analytics (baseline)
finops-agent investigate

# V3 — same playbook over MCP (client → server → analytics)
finops-agent investigate-mcp

# V4 — planner chooses MCP tools from a natural-language question
finops-agent ask "Where is our cloud money going, and are there anomalies?"

# V5 — web dashboard (charts + ask-the-agent)
finops-agent serve
# open http://127.0.0.1:8000

# Individual tools via CLI (direct analytics)
finops-agent breakdown --group-by service
finops-agent anomalies --group-by service --sensitivity 2.5
finops-agent underutilized --cpu-threshold 20
finops-agent simulate rightsize
finops-agent simulate delete_idle
```

Or run the demo scripts:

```bash
PYTHONPATH=src python3 demos/run_investigation.py      # V1
PYTHONPATH=src python3 demos/run_mcp_client.py         # V2 client only
PYTHONPATH=src python3 demos/run_mcp_investigation.py  # V3 MCP agent
PYTHONPATH=src python3 demos/run_llm_agent.py          # V4 planner agent
```

## Version 1 → 2 → 3 → 4 → 5

| Version | Entry point | How tools are invoked |
|---|---|---|
| **V1 (baseline)** | `finops_agent.agent.FinOpsAgent` | Direct Python calls into `analytics.py` |
| **V2 (MCP client)** | `finops_agent.mcp_client.FinOpsMCPClient` | Spawns MCP server over stdio, discovers tools, calls by name |
| **V3 (MCP agent)** | `finops_agent.mcp_agent.MCPFinOpsAgent` | Fixed investigation playbook over MCP |
| **V4 (LLM agent)** | `finops_agent.llm_agent.LLMFinOpsAgent` | Planner chooses tools dynamically from a question, then calls MCP |
| **V5 (dashboard)** | `finops_agent.web.app` / `finops-agent serve` | Browser UI: overview charts + V4 ask over MCP |

V1 is intentionally unchanged. V2–V4 do **not** import analytics functions.

### MCP client demo (V2)

```bash
PYTHONPATH=src python3 demos/run_mcp_client.py
```

### MCP agent demo (V3)

```bash
finops-agent investigate-mcp
# or
PYTHONPATH=src python3 demos/run_mcp_investigation.py
```

```python
import asyncio
from finops_agent.mcp_agent import MCPFinOpsAgent

async def main():
    agent = MCPFinOpsAgent()
    trace = await agent.investigate()
    print(agent.format_markdown(trace))

asyncio.run(main())
```

### LLM / offline planner agent (V4)

```bash
# Offline planner (default when OPENAI_API_KEY is unset) — still uses real MCP tools
finops-agent ask "Any unusual spending anomalies?" --planner offline

# Real OpenAI-compatible tool calling
export OPENAI_API_KEY=sk-...
finops-agent ask "Summarize waste and simulate rightsizing savings" --planner openai --model gpt-4o-mini
```

```python
import asyncio
from finops_agent.llm_agent import LLMFinOpsAgent

async def main():
    agent = LLMFinOpsAgent(planner_kind="offline")  # or "openai" / "auto"
    trace = await agent.ask("Where is spend going by service?")
    print(agent.format_markdown(trace))

asyncio.run(main())
```

### Web dashboard (V5)

```bash
finops-agent serve --host 127.0.0.1 --port 8000
# or
finops-dashboard
```

Open `http://127.0.0.1:8000` for:
- spend-by-service chart, anomaly list, underutilized resources
- ask box that runs the V4 planner → MCP tool loop
- live tool-call trace under each answer

API surfaces used by the UI:
- `GET /api/overview`
- `POST /api/ask`

## MCP server (Cursor / Claude Desktop)

This repo includes [`.cursor/mcp.json`](.cursor/mcp.json). After `pip install -e .`, the server starts with:

```bash
PYTHONPATH=src python -m finops_agent.mcp_server
# or
finops-mcp
```

Example agent prompts once the MCP server is connected:

- “Break down our cloud costs by service and highlight the top 3.”
- “Detect cost anomalies in the last 30 days.”
- “Which resources are underutilized and what would we save by rightsizing?”
- “Simulate savings from deleting idle volumes and applying S3 Intelligent-Tiering.”

## Sample data

[`data/sample_billing.json`](data/sample_billing.json) is a synthetic 90-day AWS-like dataset (~$45k) with:

- Multi-service line items (EC2, RDS, S3, EKS, NAT, ELB, Lambda, …)
- Daily utilization (CPU / memory) for compute
- **Planted anomalies** (late-period data-transfer / S3 / CloudWatch spike)
- **Underutilized** instances, idle ELB, unattached EBS, staging cache

Regenerate with:

```bash
python scripts/generate_sample_data.py
```

## Architecture

```
src/finops_agent/
  models.py        # Pydantic schemas (structured I/O)
  data_store.py    # Billing/utilization loader + filters
  analytics.py     # get_cost_breakdown, detect_anomaly, simulate_savings, …
  mcp_server.py    # MCP tool surface (MCPServer)
  mcp_client.py    # V2 MCP client (stdio discover + call_tool)
  mcp_agent.py     # V3 investigation agent over MCP
  planners.py      # V4 OpenAI + offline planners
  llm_agent.py     # V4 planner↔MCP tool loop
  web/             # V5 FastAPI dashboard + static UI
  agent.py         # V1 deterministic investigation agent + trace
  cli.py           # finops-agent CLI
```

All tool responses are **typed Pydantic models** serialized to JSON — ideal for agent reasoning loops.

## Tests

```bash
pip install -e ".[dev]"
pytest -q
```

## License

MIT
