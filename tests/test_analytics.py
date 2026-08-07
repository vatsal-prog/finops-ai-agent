"""Tests for FinOps analytics and agent orchestration."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from finops_agent.agent import FinOpsAgent
from finops_agent.analytics import (
    build_optimization_report,
    detect_anomaly,
    find_underutilized_resources,
    get_cost_breakdown,
    simulate_savings,
)
from finops_agent.data_store import clear_dataset_cache, load_dataset

DATA = Path(__file__).resolve().parents[1] / "data" / "sample_billing.json"


@pytest.fixture(autouse=True)
def _fresh_cache():
    clear_dataset_cache()
    yield
    clear_dataset_cache()


def test_dataset_loads():
    ds = load_dataset(str(DATA))
    assert ds.account_id == "123456789012"
    assert len(ds.daily_costs) > 1000
    assert len(ds.utilization) > 100


def test_cost_breakdown_by_service():
    result = get_cost_breakdown(group_by="service", data_path=str(DATA))
    assert result.total_cost_usd > 0
    assert result.buckets
    keys = [b.key for b in result.buckets]
    assert "EC2" in keys or "S3" in keys or "EKS" in keys
    assert abs(sum(b.share_pct for b in result.buckets) - 100.0) < 0.5


def test_cost_breakdown_filters():
    result = get_cost_breakdown(
        group_by="resource",
        service="EC2",
        region="us-east-1",
        data_path=str(DATA),
    )
    assert result.total_cost_usd > 0
    assert all(b.resource_count >= 1 for b in result.buckets)


def test_detect_anomaly_finds_spike():
    # Default 30-day window catches the planted late-period S3/NAT/CloudWatch spike
    result = detect_anomaly(
        lookback_days=30,
        sensitivity=2.5,
        group_by="service",
        data_path=str(DATA),
    )
    assert result.anomalies, "expected planted anomalies to be detected"
    services = {a.service for a in result.anomalies if a.service}
    assert services & {"S3", "VPC", "CloudWatch"}


def test_find_underutilized_resources():
    result = find_underutilized_resources(
        cpu_threshold_pct=20.0,
        lookback_days=30,
        data_path=str(DATA),
    )
    assert result.resources
    assert result.total_monthly_savings_usd > 0
    ids = {r.resource_id for r in result.resources}
    # Known underutilized / idle resources from sample data
    assert ids & {
        "i-0a1b2c3d4e5f6001",
        "i-0a1b2c3d4e5f6003",
        "vol-0idle00000001",
        "vol-0idle00000002",
        "cache-redis-staging",
        "db-dev-mysql-1",
    }


def test_simulate_savings_rightsize():
    result = simulate_savings(action="rightsize", lookback_days=30, data_path=str(DATA))
    assert result.scenarios
    assert result.total_monthly_savings_usd > 0
    assert result.scenarios[0].affected_resources


def test_simulate_savings_delete_idle():
    result = simulate_savings(action="delete_idle", lookback_days=30, data_path=str(DATA))
    assert result.total_monthly_savings_usd > 0


def test_simulate_savings_s3():
    result = simulate_savings(
        action="s3_intelligent_tiering", lookback_days=30, data_path=str(DATA)
    )
    assert result.total_monthly_savings_usd > 0
    assert result.scenarios[0].savings_pct == 40.0


def test_optimization_report():
    report = build_optimization_report(lookback_days=30, data_path=str(DATA))
    assert report.total_cost_usd > 0
    assert report.recommendations
    assert report.potential_monthly_savings_usd > 0
    assert "acme-production" in report.narrative


def test_agent_investigate_trace():
    agent = FinOpsAgent(data_path=str(DATA))
    trace = agent.investigate(lookback_days=30)
    tool_names = [s.name for s in trace.steps]
    assert "get_cost_breakdown" in tool_names
    assert "detect_anomaly" in tool_names
    assert "find_underutilized_resources" in tool_names
    assert "simulate_savings" in tool_names
    assert "generate_optimization_report" in tool_names
    assert trace.report is not None
    md = agent.format_markdown(trace)
    assert "FinOps Agent Investigation Report" in md
    payload = trace.to_dict()
    assert payload["report"]["account_name"] == "acme-production"
    # Ensure JSON-serializable
    json.dumps(payload)


def test_mcp_server_imports():
    from finops_agent.mcp_server import server

    tools = server._tool_manager.list_tools()
    names = {t.name for t in tools}
    assert {
        "get_cost_breakdown",
        "detect_anomaly",
        "simulate_savings",
        "find_underutilized_resources",
        "generate_optimization_report",
    } <= names
