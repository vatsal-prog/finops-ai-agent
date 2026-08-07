"""FinOps analytics: cost breakdown, anomalies, underutilization, savings sims."""

from __future__ import annotations

from collections import defaultdict
from statistics import mean, pstdev
from typing import Literal

from finops_agent.data_store import (
    filter_costs,
    filter_utilization,
    load_dataset,
    prior_window,
    resolve_window,
)
from finops_agent.models import (
    AnomalyEvent,
    AnomalyResult,
    BreakdownBucket,
    CostBreakdownResult,
    CostLineItem,
    GroupBy,
    OptimizationReport,
    Recommendation,
    SavingsAction,
    SavingsScenario,
    SavingsSimulationResult,
    UnderutilizedResource,
    UnderutilizedResult,
)

AnomalyGroupBy = Literal["day", "service", "region", "resource"]


def _safe_pct(numerator: float, denominator: float) -> float:
    if denominator == 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 2)


def _severity(z: float) -> Literal["low", "medium", "high", "critical"]:
    az = abs(z)
    if az >= 4.0:
        return "critical"
    if az >= 3.0:
        return "high"
    if az >= 2.5:
        return "medium"
    return "low"


def _group_key(row: CostLineItem, group_by: GroupBy) -> str:
    if group_by == "service":
        return row.service
    if group_by == "region":
        return row.region
    if group_by == "resource":
        return row.resource_id
    if group_by == "team":
        return row.tags.get("team", "untagged")
    if group_by == "env":
        return row.tags.get("env", "untagged")
    return row.date


def get_cost_breakdown(
    *,
    group_by: GroupBy = "service",
    start_date: str | None = None,
    end_date: str | None = None,
    service: str | None = None,
    region: str | None = None,
    team: str | None = None,
    env: str | None = None,
    data_path: str | None = None,
) -> CostBreakdownResult:
    """Aggregate billing line items into a structured cost breakdown."""
    dataset = load_dataset(data_path)
    start, end = resolve_window(dataset, start_date=start_date, end_date=end_date)
    rows = filter_costs(
        dataset,
        start_date=start,
        end_date=end,
        service=service,
        region=region,
        team=team,
        env=env,
    )
    total = sum(r.cost_usd for r in rows)

    groups: dict[str, list[CostLineItem]] = defaultdict(list)
    for row in rows:
        groups[_group_key(row, group_by)].append(row)

    p_start, p_end = prior_window(start, end)
    prior_rows = filter_costs(
        dataset,
        start_date=p_start,
        end_date=p_end,
        service=service,
        region=region,
        team=team,
        env=env,
    )
    prior_total = sum(r.cost_usd for r in prior_rows) if prior_rows else None
    prior_by_key: dict[str, float] = defaultdict(float)
    for row in prior_rows:
        prior_by_key[_group_key(row, group_by)] += row.cost_usd

    buckets: list[BreakdownBucket] = []
    for key, items in groups.items():
        cost = sum(i.cost_usd for i in items)
        resources = {i.resource_id for i in items}
        prior_cost = prior_by_key.get(key)
        trend = None
        if prior_cost is not None and prior_cost > 0:
            trend = round(((cost - prior_cost) / prior_cost) * 100.0, 2)
        buckets.append(
            BreakdownBucket(
                key=key,
                cost_usd=round(cost, 2),
                share_pct=_safe_pct(cost, total),
                resource_count=len(resources),
                trend_pct=trend,
            )
        )
    buckets.sort(key=lambda b: b.cost_usd, reverse=True)

    pop = None
    if prior_total and prior_total > 0:
        pop = round(((total - prior_total) / prior_total) * 100.0, 2)

    return CostBreakdownResult(
        group_by=group_by,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        total_cost_usd=round(total, 2),
        currency=dataset.currency,
        buckets=buckets,
        filters={"service": service, "region": region, "team": team, "env": env},
        prior_period_total_usd=round(prior_total, 2) if prior_total is not None else None,
        period_over_period_pct=pop,
    )


def detect_anomaly(
    *,
    lookback_days: int = 30,
    sensitivity: float = 2.5,
    group_by: AnomalyGroupBy = "service",
    service: str | None = None,
    region: str | None = None,
    end_date: str | None = None,
    data_path: str | None = None,
) -> AnomalyResult:
    """Detect unusual spending using z-score vs a rolling baseline.

    Args:
        lookback_days: Window of recent history to analyze.
        sensitivity: Z-score threshold (lower = more alerts). Typical: 2.0–3.0.
        group_by: Dimension to evaluate (day, service, region, or resource).
        service: Optional service filter.
        region: Optional region filter.
        end_date: Optional analysis end date (YYYY-MM-DD).
        data_path: Optional path to billing JSON.
    """
    dataset = load_dataset(data_path)
    start, end = resolve_window(
        dataset, end_date=end_date, lookback_days=max(lookback_days, 14)
    )
    rows = filter_costs(dataset, start_date=start, end_date=end, service=service, region=region)

    series: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for row in rows:
        if group_by == "day":
            dim = "total"
        elif group_by == "service":
            dim = row.service
        elif group_by == "region":
            dim = row.region
        else:
            dim = row.resource_id
        series[dim][row.date] += row.cost_usd

    all_daily: dict[str, float] = defaultdict(float)
    for row in rows:
        all_daily[row.date] += row.cost_usd
    daily_values = list(all_daily.values())
    baseline_mean = mean(daily_values) if daily_values else 0.0
    baseline_std = pstdev(daily_values) if len(daily_values) > 1 else 0.0

    anomalies: list[AnomalyEvent] = []
    for dim, by_date in series.items():
        values = list(by_date.values())
        if len(values) < 7:
            continue
        m = mean(values)
        s = pstdev(values)
        if s == 0:
            continue
        for day, actual in sorted(by_date.items()):
            z = (actual - m) / s
            if z < sensitivity:
                continue
            sev = _severity(z)
            if group_by == "day":
                anomalies.append(
                    AnomalyEvent(
                        date=day,
                        actual_cost_usd=round(actual, 2),
                        expected_cost_usd=round(m, 2),
                        z_score=round(z, 2),
                        severity=sev,
                        message=(
                            f"Total spend ${actual:,.2f} on {day} is {z:.1f}σ above "
                            f"baseline ${m:,.2f}/day"
                        ),
                    )
                )
            elif group_by == "service":
                anomalies.append(
                    AnomalyEvent(
                        date=day,
                        service=dim,
                        actual_cost_usd=round(actual, 2),
                        expected_cost_usd=round(m, 2),
                        z_score=round(z, 2),
                        severity=sev,
                        message=f"{dim} spiked to ${actual:,.2f} on {day} ({z:.1f}σ)",
                    )
                )
            elif group_by == "region":
                anomalies.append(
                    AnomalyEvent(
                        date=day,
                        region=dim,
                        actual_cost_usd=round(actual, 2),
                        expected_cost_usd=round(m, 2),
                        z_score=round(z, 2),
                        severity=sev,
                        message=f"Region {dim} spend ${actual:,.2f} on {day} ({z:.1f}σ)",
                    )
                )
            else:
                anomalies.append(
                    AnomalyEvent(
                        date=day,
                        resource_id=dim,
                        actual_cost_usd=round(actual, 2),
                        expected_cost_usd=round(m, 2),
                        z_score=round(z, 2),
                        severity=sev,
                        message=f"Resource {dim} cost ${actual:,.2f} on {day} ({z:.1f}σ)",
                    )
                )

    anomalies.sort(key=lambda a: (a.z_score, a.actual_cost_usd), reverse=True)
    if group_by == "day":
        seen: set[str] = set()
        deduped: list[AnomalyEvent] = []
        for event in anomalies:
            if event.date in seen:
                continue
            seen.add(event.date)
            deduped.append(event)
        anomalies = deduped

    summary = (
        f"Found {len(anomalies)} anomal{'y' if len(anomalies) == 1 else 'ies'} "
        f"at z≥{sensitivity} over {lookback_days} days "
        f"(baseline ${baseline_mean:,.2f}/day ± ${baseline_std:,.2f})."
    )
    return AnomalyResult(
        lookback_days=lookback_days,
        sensitivity=sensitivity,
        baseline_mean_usd=round(baseline_mean, 2),
        baseline_std_usd=round(baseline_std, 2),
        threshold_z=sensitivity,
        anomalies=anomalies,
        summary=summary,
    )


def find_underutilized_resources(
    *,
    cpu_threshold_pct: float = 20.0,
    lookback_days: int = 30,
    end_date: str | None = None,
    data_path: str | None = None,
) -> UnderutilizedResult:
    """Find compute resources with persistently low utilization."""
    dataset = load_dataset(data_path)
    start, end = resolve_window(dataset, end_date=end_date, lookback_days=lookback_days)
    util_rows = filter_utilization(dataset, start_date=start, end_date=end)
    cost_rows = filter_costs(dataset, start_date=start, end_date=end)

    cost_by_resource: dict[str, float] = defaultdict(float)
    meta: dict[str, CostLineItem] = {}
    for row in cost_rows:
        cost_by_resource[row.resource_id] += row.cost_usd
        meta[row.resource_id] = row

    cpu_by_resource: dict[str, list[float]] = defaultdict(list)
    mem_by_resource: dict[str, list[float]] = defaultdict(list)
    status_by_resource: dict[str, str] = {}
    for row in util_rows:
        status_by_resource[row.resource_id] = row.status
        if row.cpu_utilization_pct is not None:
            cpu_by_resource[row.resource_id].append(row.cpu_utilization_pct)
        if row.memory_utilization_pct is not None:
            mem_by_resource[row.resource_id].append(row.memory_utilization_pct)

    days = max((end - start).days + 1, 1)
    results: list[UnderutilizedResource] = []
    hints = dataset.pricing_hints.get("rightsizing_map", {})

    for rid, cpus in cpu_by_resource.items():
        avg_cpu = mean(cpus)
        avg_mem = mean(mem_by_resource[rid]) if mem_by_resource.get(rid) else None
        status = status_by_resource.get(rid, "unknown")
        period_cost = cost_by_resource.get(rid, 0.0)
        monthly_cost = period_cost / days * 30.0
        item = meta.get(rid)
        if item is None:
            continue

        low_cpu = avg_cpu < cpu_threshold_pct
        low_mem = avg_mem is not None and avg_mem < cpu_threshold_pct
        low_elb = item.service == "ELB" and avg_cpu < 5.0

        if not (low_cpu or low_mem or low_elb or status == "stopped"):
            continue

        itype = item.instance_type
        rightsize = hints.get(itype)
        if status == "stopped":
            rec = "Stopped compute may still incur attached storage — review and terminate if unused."
            savings = monthly_cost * 0.5
        elif low_elb:
            rec = "Idle load balancer with near-zero traffic — migrate to ALB or delete."
            savings = monthly_cost * 0.9
        elif rightsize:
            rec = f"Rightsize {itype} → {rightsize['to']} (avg CPU {avg_cpu:.1f}%)."
            new_daily = float(rightsize["daily_cost"])
            savings = max(0.0, monthly_cost - new_daily * 30.0)
        else:
            rec = f"Low utilization (CPU {avg_cpu:.1f}%) — consider rightsizing or scheduling."
            savings = monthly_cost * 0.4

        results.append(
            UnderutilizedResource(
                resource_id=rid,
                service=item.service,
                region=item.region,
                instance_type=itype,
                avg_cpu_pct=round(avg_cpu, 2),
                avg_memory_pct=round(avg_mem, 2) if avg_mem is not None else None,
                period_cost_usd=round(period_cost, 2),
                status=status,
                recommendation=rec,
                estimated_monthly_savings_usd=round(savings, 2),
            )
        )

    for resource in dataset.resources:
        if resource.service != "EBS" or resource.status != "available":
            continue
        period_cost = cost_by_resource.get(
            resource.resource_id, resource.baseline_daily_cost_usd * days
        )
        monthly = period_cost / days * 30.0
        results.append(
            UnderutilizedResource(
                resource_id=resource.resource_id,
                service="EBS",
                region=resource.region,
                instance_type=resource.instance_type,
                avg_cpu_pct=None,
                avg_memory_pct=None,
                period_cost_usd=round(period_cost, 2),
                status=resource.status,
                recommendation="Unattached EBS volume — snapshot then delete to eliminate waste.",
                estimated_monthly_savings_usd=round(monthly, 2),
            )
        )

    results.sort(key=lambda r: r.estimated_monthly_savings_usd, reverse=True)
    total_savings = round(sum(r.estimated_monthly_savings_usd for r in results), 2)
    summary = (
        f"Identified {len(results)} underutilized/idle resources with "
        f"~${total_savings:,.2f}/mo potential savings "
        f"(CPU threshold {cpu_threshold_pct}%, lookback {lookback_days}d)."
    )
    return UnderutilizedResult(
        cpu_threshold_pct=cpu_threshold_pct,
        lookback_days=lookback_days,
        resources=results,
        total_monthly_savings_usd=total_savings,
        summary=summary,
    )


def simulate_savings(
    *,
    action: SavingsAction,
    resource_ids: list[str] | None = None,
    lookback_days: int = 30,
    discount_pct: float | None = None,
    end_date: str | None = None,
    data_path: str | None = None,
) -> SavingsSimulationResult:
    """What-if simulation for common FinOps optimization actions."""
    dataset = load_dataset(data_path)
    start, end = resolve_window(dataset, end_date=end_date, lookback_days=lookback_days)
    days = max((end - start).days + 1, 1)
    rows = filter_costs(dataset, start_date=start, end_date=end)
    hints = dataset.pricing_hints

    cost_by_resource: dict[str, float] = defaultdict(float)
    meta: dict[str, CostLineItem] = {}
    for row in rows:
        cost_by_resource[row.resource_id] += row.cost_usd
        meta[row.resource_id] = row

    under = find_underutilized_resources(
        lookback_days=lookback_days, end_date=end_date, data_path=data_path
    )
    under_ids = {r.resource_id for r in under.resources}
    under_map = {r.resource_id: r for r in under.resources}

    def monthly(cost: float) -> float:
        return cost / days * 30.0

    scenarios: list[SavingsScenario] = []

    if action == "rightsize":
        targets = resource_ids or [
            r.resource_id
            for r in under.resources
            if r.service in {"EC2", "RDS", "ElastiCache"} and r.avg_cpu_pct is not None
        ]
        rightsizing = hints.get("rightsizing_map", {})
        affected: list[str] = []
        current = 0.0
        projected = 0.0
        for rid in targets:
            item = meta.get(rid)
            if not item:
                continue
            mapping = rightsizing.get(item.instance_type)
            if not mapping:
                continue
            m_cost = monthly(cost_by_resource[rid])
            new_cost = float(mapping["daily_cost"]) * 30.0
            if new_cost >= m_cost:
                continue
            affected.append(rid)
            current += m_cost
            projected += new_cost
        savings = current - projected
        scenarios.append(
            SavingsScenario(
                action=action,
                description="Rightsize oversized compute based on observed CPU/memory.",
                affected_resources=affected,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=_safe_pct(savings, current),
                assumptions=[
                    "Workload fits target instance family after rightsizing.",
                    "Utilization remains similar post-change.",
                ],
                risks=["Transient CPU spikes may require burst capacity or ASG headroom."],
            )
        )

    elif action == "reserved_instances":
        pct = discount_pct if discount_pct is not None else float(
            hints.get("reserved_instance_discount_pct", 35)
        )
        targets = resource_ids or [
            rid
            for rid, item in meta.items()
            if item.service in {"EC2", "RDS"}
            and item.tags.get("env") == "prod"
            and rid not in under_ids
        ]
        current = sum(monthly(cost_by_resource[rid]) for rid in targets if rid in cost_by_resource)
        projected = current * (1 - pct / 100.0)
        savings = current - projected
        scenarios.append(
            SavingsScenario(
                action=action,
                description=f"Cover stable prod compute with 1-year Reserved Instances (−{pct:.0f}%).",
                affected_resources=targets,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=round(pct, 2),
                assumptions=["Instances run ≥1 year", "No major architecture migration planned"],
                risks=["Upfront commitment reduces flexibility if demand drops."],
            )
        )

    elif action == "savings_plan":
        pct = discount_pct if discount_pct is not None else float(
            hints.get("savings_plan_discount_pct", 28)
        )
        eligible_services = {"EC2", "Lambda", "Fargate", "EKS"}
        targets = resource_ids or [
            rid for rid, item in meta.items() if item.service in eligible_services
        ]
        current = sum(monthly(cost_by_resource[rid]) for rid in targets if rid in cost_by_resource)
        projected = current * (1 - pct / 100.0)
        savings = current - projected
        scenarios.append(
            SavingsScenario(
                action=action,
                description=f"Compute Savings Plan covering EC2/Lambda/EKS (−{pct:.0f}%).",
                affected_resources=targets,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=round(pct, 2),
                assumptions=["Commit to consistent $/hour compute spend"],
                risks=["Under-commitment leaves on-demand residual; over-commitment wastes commit."],
            )
        )

    elif action == "delete_idle":
        targets = resource_ids or [
            r.resource_id
            for r in under.resources
            if r.service in {"EBS", "ELB"} or r.status in {"available", "stopped"}
        ]
        current = 0.0
        for rid in targets:
            if rid in under_map:
                current += under_map[rid].estimated_monthly_savings_usd
            elif rid in cost_by_resource:
                current += monthly(cost_by_resource[rid])
        projected = 0.0
        savings = current
        scenarios.append(
            SavingsScenario(
                action=action,
                description="Delete unattached volumes, idle LBs, and abandoned stopped resources.",
                affected_resources=targets,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=100.0 if current else 0.0,
                assumptions=["Snapshots retained for recoverable volumes", "No hidden dependencies"],
                risks=["Accidental deletion of rarely-used disaster-recovery assets."],
            )
        )

    elif action == "s3_intelligent_tiering":
        pct = discount_pct if discount_pct is not None else float(
            hints.get("s3_intelligent_tiering_discount_pct", 40)
        )
        targets = resource_ids or [rid for rid, item in meta.items() if item.service == "S3"]
        current = sum(monthly(cost_by_resource[rid]) for rid in targets if rid in cost_by_resource)
        projected = current * (1 - pct / 100.0)
        savings = current - projected
        scenarios.append(
            SavingsScenario(
                action=action,
                description=f"Move infrequently accessed S3 data to Intelligent-Tiering (−{pct:.0f}%).",
                affected_resources=targets,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=round(pct, 2),
                assumptions=["≥40% of objects are infrequently accessed"],
                risks=["Monitoring fees on small buckets may offset savings."],
            )
        )

    elif action == "stop_underutilized":
        targets = resource_ids or [
            r.resource_id
            for r in under.resources
            if r.service == "EC2" and (r.avg_cpu_pct or 0) < 15
        ]
        current = sum(monthly(cost_by_resource[rid]) for rid in targets if rid in cost_by_resource)
        projected = current * 0.4
        savings = current - projected
        scenarios.append(
            SavingsScenario(
                action=action,
                description="Schedule stop/start for low-util non-critical EC2 (nights + weekends).",
                affected_resources=targets,
                current_monthly_cost_usd=round(current, 2),
                projected_monthly_cost_usd=round(projected, 2),
                monthly_savings_usd=round(savings, 2),
                annual_savings_usd=round(savings * 12, 2),
                savings_pct=_safe_pct(savings, current),
                assumptions=["Workloads tolerate scheduled downtime", "EBS remains attached"],
                risks=["Cold-start delays; stateful apps need drain hooks."],
            )
        )

    else:
        raise ValueError(f"Unsupported action: {action}")

    total_monthly = round(sum(s.monthly_savings_usd for s in scenarios), 2)
    total_annual = round(sum(s.annual_savings_usd for s in scenarios), 2)
    affected_count = sum(len(s.affected_resources) for s in scenarios)
    summary = (
        f"Action '{action}' yields ~${total_monthly:,.2f}/mo "
        f"(${total_annual:,.2f}/yr) across {affected_count} resources."
    )
    return SavingsSimulationResult(
        action=action,
        scenarios=scenarios,
        total_monthly_savings_usd=total_monthly,
        total_annual_savings_usd=total_annual,
        summary=summary,
    )


def build_optimization_report(
    *,
    lookback_days: int = 30,
    data_path: str | None = None,
) -> OptimizationReport:
    """Compose a full FinOps optimization report from structured tool outputs."""
    dataset = load_dataset(data_path)
    breakdown = get_cost_breakdown(group_by="service", data_path=data_path)
    anomalies = detect_anomaly(
        lookback_days=lookback_days, group_by="service", data_path=data_path
    )
    under = find_underutilized_resources(lookback_days=lookback_days, data_path=data_path)

    sims = [
        simulate_savings(action="delete_idle", lookback_days=lookback_days, data_path=data_path),
        simulate_savings(action="rightsize", lookback_days=lookback_days, data_path=data_path),
        simulate_savings(
            action="s3_intelligent_tiering", lookback_days=lookback_days, data_path=data_path
        ),
        simulate_savings(
            action="reserved_instances", lookback_days=lookback_days, data_path=data_path
        ),
    ]

    recommendations: list[Recommendation] = []
    for idx, scenario in enumerate(
        (s for sim in sims for s in sim.scenarios if s.monthly_savings_usd >= 1),
        start=1,
    ):
        priority: Literal["P0", "P1", "P2", "P3"]
        if scenario.monthly_savings_usd >= 500:
            priority = "P0"
        elif scenario.monthly_savings_usd >= 200:
            priority = "P1"
        elif scenario.monthly_savings_usd >= 50:
            priority = "P2"
        else:
            priority = "P3"
        recommendations.append(
            Recommendation(
                id=f"rec-{idx:03d}",
                priority=priority,
                category=scenario.action,
                title=scenario.description,
                detail=(
                    f"Current ${scenario.current_monthly_cost_usd:,.2f}/mo → "
                    f"${scenario.projected_monthly_cost_usd:,.2f}/mo. "
                    f"Assumptions: {'; '.join(scenario.assumptions)}"
                ),
                estimated_monthly_savings_usd=scenario.monthly_savings_usd,
                resource_ids=scenario.affected_resources[:12],
                action=scenario.action,
            )
        )

    recommendations.sort(
        key=lambda r: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}[r.priority],
            -r.estimated_monthly_savings_usd,
        )
    )

    potential = round(sum(r.estimated_monthly_savings_usd for r in recommendations), 2)
    top = breakdown.buckets[:3]
    top_str = ", ".join(f"{b.key} (${b.cost_usd:,.2f})" for b in top)
    narrative = (
        f"Account {dataset.account_name} ({dataset.account_id}) spent "
        f"${breakdown.total_cost_usd:,.2f} from {breakdown.start_date} to {breakdown.end_date}. "
        f"Top services: {top_str}. "
        f"{anomalies.summary} {under.summary} "
        f"Combined modeled optimizations suggest ~${potential:,.2f}/mo in savings "
        f"(actions may overlap — validate before stacking)."
    )

    return OptimizationReport(
        account_id=dataset.account_id,
        account_name=dataset.account_name,
        period={"start": breakdown.start_date, "end": breakdown.end_date},
        total_cost_usd=breakdown.total_cost_usd,
        anomaly_count=len(anomalies.anomalies),
        underutilized_count=len(under.resources),
        recommendations=recommendations,
        potential_monthly_savings_usd=potential,
        narrative=narrative,
    )
