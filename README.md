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
┌─────────────────┐     MCP (stdio)     ┌──────────────────────────┐
│  Cursor / LLM   │ ◄─────────────────► │  finops-agent MCP server │
│  or CLI agent   │                     │  + analytics engine      │
└─────────────────┘                     └────────────┬─────────────┘
                                                     │
                                          ┌──────────▼──────────┐
                                          │ sample_billing.json │
                                          │ (structured costs + │
                                          │  utilization series)│
                                          └─────────────────────┘
```

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Full agent investigation (deterministic playbook, no LLM required)
finops-agent investigate

# Individual tools via CLI
finops-agent breakdown --group-by service
finops-agent anomalies --group-by service --sensitivity 2.5
finops-agent underutilized --cpu-threshold 20
finops-agent simulate rightsize
finops-agent simulate delete_idle
```

Or run the demo script:

```bash
PYTHONPATH=src python demos/run_investigation.py
```

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
- **Planted anomalies** (days 45–48 data-transfer / S3 / CloudWatch spike)
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
  agent.py         # Deterministic investigation agent + trace
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
