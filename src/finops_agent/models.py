"""Pydantic models for FinOps structured data and tool responses."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


GroupBy = Literal["service", "region", "resource", "team", "env", "day"]
SavingsAction = Literal[
    "rightsize",
    "reserved_instances",
    "savings_plan",
    "delete_idle",
    "s3_intelligent_tiering",
    "stop_underutilized",
]


class CostLineItem(BaseModel):
    date: str
    resource_id: str
    service: str
    region: str
    instance_type: str
    cost_usd: float
    tags: dict[str, str] = Field(default_factory=dict)


class UtilizationPoint(BaseModel):
    date: str
    resource_id: str
    service: str
    region: str
    cpu_utilization_pct: float | None = None
    memory_utilization_pct: float | None = None
    network_in_gb: float | None = None
    status: str = "unknown"


class ResourceMeta(BaseModel):
    resource_id: str
    service: str
    region: str
    instance_type: str
    baseline_daily_cost_usd: float
    status: str


class BillingDataset(BaseModel):
    account_id: str
    account_name: str
    currency: str = "USD"
    period: dict[str, str]
    cloud_provider: str = "AWS"
    resources: list[ResourceMeta]
    daily_costs: list[CostLineItem]
    utilization: list[UtilizationPoint]
    pricing_hints: dict[str, Any] = Field(default_factory=dict)


class BreakdownBucket(BaseModel):
    key: str
    cost_usd: float
    share_pct: float
    resource_count: int = 0
    trend_pct: float | None = None


class CostBreakdownResult(BaseModel):
    group_by: GroupBy
    start_date: str
    end_date: str
    total_cost_usd: float
    currency: str
    buckets: list[BreakdownBucket]
    filters: dict[str, str | None] = Field(default_factory=dict)
    prior_period_total_usd: float | None = None
    period_over_period_pct: float | None = None


class AnomalyEvent(BaseModel):
    date: str
    service: str | None = None
    region: str | None = None
    resource_id: str | None = None
    actual_cost_usd: float
    expected_cost_usd: float
    z_score: float
    severity: Literal["low", "medium", "high", "critical"]
    message: str


class AnomalyResult(BaseModel):
    lookback_days: int
    sensitivity: float
    baseline_mean_usd: float
    baseline_std_usd: float
    threshold_z: float
    anomalies: list[AnomalyEvent]
    summary: str


class UnderutilizedResource(BaseModel):
    resource_id: str
    service: str
    region: str
    instance_type: str
    avg_cpu_pct: float | None
    avg_memory_pct: float | None
    period_cost_usd: float
    status: str
    recommendation: str
    estimated_monthly_savings_usd: float


class UnderutilizedResult(BaseModel):
    cpu_threshold_pct: float
    lookback_days: int
    resources: list[UnderutilizedResource]
    total_monthly_savings_usd: float
    summary: str


class SavingsScenario(BaseModel):
    action: SavingsAction
    description: str
    affected_resources: list[str]
    current_monthly_cost_usd: float
    projected_monthly_cost_usd: float
    monthly_savings_usd: float
    annual_savings_usd: float
    savings_pct: float
    assumptions: list[str]
    risks: list[str]


class SavingsSimulationResult(BaseModel):
    action: SavingsAction
    scenarios: list[SavingsScenario]
    total_monthly_savings_usd: float
    total_annual_savings_usd: float
    summary: str


class Recommendation(BaseModel):
    id: str
    priority: Literal["P0", "P1", "P2", "P3"]
    category: str
    title: str
    detail: str
    estimated_monthly_savings_usd: float
    resource_ids: list[str] = Field(default_factory=list)
    action: SavingsAction | None = None


class OptimizationReport(BaseModel):
    account_id: str
    account_name: str
    period: dict[str, str]
    total_cost_usd: float
    anomaly_count: int
    underutilized_count: int
    recommendations: list[Recommendation]
    potential_monthly_savings_usd: float
    narrative: str
