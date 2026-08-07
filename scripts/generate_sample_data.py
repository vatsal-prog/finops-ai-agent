#!/usr/bin/env python3
"""Regenerate data/sample_billing.json with realistic FinOps demo data."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

START = date(2026, 1, 1)
DAYS = 90
END = START + timedelta(days=DAYS - 1)

RESOURCES = [
    ("i-0a1b2c3d4e5f6001", "EC2", "us-east-1", "m5.2xlarge", 18.40, 8.5, 12.0, "running"),
    ("i-0a1b2c3d4e5f6002", "EC2", "us-east-1", "m5.xlarge", 9.20, 62.0, 71.0, "running"),
    ("i-0a1b2c3d4e5f6003", "EC2", "us-west-2", "c5.4xlarge", 32.80, 11.2, 18.5, "running"),
    ("i-0a1b2c3d4e5f6004", "EC2", "eu-west-1", "t3.medium", 2.10, 45.0, 55.0, "running"),
    ("i-0a1b2c3d4e5f6005", "EC2", "us-east-1", "r5.large", 6.05, 4.0, 22.0, "running"),
    ("i-0stopped0000001", "EC2", "us-east-1", "m5.large", 0.0, 0.0, 0.0, "stopped"),
    ("vol-0idle00000001", "EBS", "us-east-1", "gp3-500GB", 4.00, None, None, "available"),
    ("vol-0idle00000002", "EBS", "us-west-2", "gp3-1TB", 8.00, None, None, "available"),
    ("db-prod-postgres-1", "RDS", "us-east-1", "db.r5.xlarge", 22.50, 35.0, 48.0, "available"),
    ("db-dev-mysql-1", "RDS", "us-east-1", "db.t3.large", 7.80, 6.5, 15.0, "available"),
    ("bucket-logs-archive", "S3", "us-east-1", "standard", 45.00, None, None, "active"),
    ("bucket-ml-training", "S3", "us-west-2", "standard", 120.00, None, None, "active"),
    ("nat-0gateway00001", "VPC", "us-east-1", "NAT Gateway", 32.40, None, None, "available"),
    ("elb-legacy-internal", "ELB", "us-east-1", "classic", 18.00, 2.0, None, "active"),
    ("lambda-api-prod", "Lambda", "us-east-1", "arm64", 8.50, None, None, "active"),
    ("lambda-batch-nightly", "Lambda", "us-east-1", "x86_64", 3.20, None, None, "active"),
    ("eks-cluster-prod", "EKS", "us-east-1", "control-plane", 72.00, None, None, "active"),
    ("cache-redis-prod", "ElastiCache", "us-east-1", "cache.r6g.large", 11.20, 28.0, 40.0, "available"),
    ("cache-redis-staging", "ElastiCache", "us-east-1", "cache.r6g.large", 11.20, 3.5, 8.0, "available"),
    ("cw-logs-ingest", "CloudWatch", "us-east-1", "logs", 28.00, None, None, "active"),
]

# Place the spike in the most recent window so default 30-day lookbacks catch it.
ANOMALY_DAYS = {DAYS - 12, DAYS - 11, DAYS - 10, DAYS - 9}  # ~last 2 weeks
ANOMALY_MULTIPLIER = {
    "bucket-ml-training": 4.5,
    "nat-0gateway00001": 3.2,
    "cw-logs-ingest": 2.8,
}


def main() -> None:
    daily_costs = []
    utilization = []

    for day_offset in range(DAYS):
        d = START + timedelta(days=day_offset)
        weekend_factor = 0.85 if d.weekday() >= 5 else 1.0
        growth = 1.0 + (day_offset * 0.0015)

        for rid, service, region, itype, base_cost, cpu, mem, status in RESOURCES:
            cost = base_cost * weekend_factor * growth
            if day_offset in ANOMALY_DAYS and rid in ANOMALY_MULTIPLIER:
                cost *= ANOMALY_MULTIPLIER[rid]
            noise = 1.0 + (((hash(f"{rid}{d}") % 21) - 10) / 500.0)
            cost = round(cost * noise, 4)

            daily_costs.append(
                {
                    "date": d.isoformat(),
                    "resource_id": rid,
                    "service": service,
                    "region": region,
                    "instance_type": itype,
                    "cost_usd": cost,
                    "tags": {
                        "env": (
                            "prod"
                            if "prod" in rid
                            or rid.startswith("i-0a1")
                            or rid.startswith(("bucket", "nat", "elb", "eks", "cw", "vol", "lambda-api"))
                            else "nonprod"
                        ),
                        "team": (
                            "platform"
                            if service in ("EKS", "VPC", "ELB", "CloudWatch")
                            else ("data" if "ml" in rid or "bucket" in rid else "engineering")
                        ),
                    },
                }
            )

            if cpu is not None:
                cpu_noise = (hash(f"cpu{rid}{d}") % 15) - 7
                mem_noise = ((hash(f"mem{rid}{d}") % 11) - 5) if mem is not None else 0
                utilization.append(
                    {
                        "date": d.isoformat(),
                        "resource_id": rid,
                        "service": service,
                        "region": region,
                        "cpu_utilization_pct": max(0.0, round(cpu + cpu_noise * 0.4, 2)),
                        "memory_utilization_pct": (
                            None if mem is None else max(0.0, round(mem + mem_noise * 0.5, 2))
                        ),
                        "network_in_gb": round(abs(hash(f"net{rid}{d}") % 100) / 10.0, 2),
                        "status": status,
                    }
                )

    payload = {
        "account_id": "123456789012",
        "account_name": "acme-production",
        "currency": "USD",
        "period": {"start": START.isoformat(), "end": END.isoformat()},
        "cloud_provider": "AWS",
        "resources": [
            {
                "resource_id": r[0],
                "service": r[1],
                "region": r[2],
                "instance_type": r[3],
                "baseline_daily_cost_usd": r[4],
                "status": r[7],
            }
            for r in RESOURCES
        ],
        "daily_costs": daily_costs,
        "utilization": utilization,
        "pricing_hints": {
            "rightsizing_map": {
                "m5.2xlarge": {
                    "to": "m5.large",
                    "daily_cost": 4.60,
                    "expected_cpu_headroom_pct": 40,
                },
                "c5.4xlarge": {
                    "to": "c5.xlarge",
                    "daily_cost": 8.20,
                    "expected_cpu_headroom_pct": 35,
                },
                "r5.large": {
                    "to": "r5.large→t3.medium",
                    "daily_cost": 2.10,
                    "expected_cpu_headroom_pct": 30,
                },
                "db.t3.large": {
                    "to": "db.t3.medium",
                    "daily_cost": 3.90,
                    "expected_cpu_headroom_pct": 40,
                },
                "cache.r6g.large": {
                    "to": "cache.t4g.medium",
                    "daily_cost": 3.50,
                    "expected_cpu_headroom_pct": 40,
                },
            },
            "reserved_instance_discount_pct": 35,
            "savings_plan_discount_pct": 28,
            "s3_intelligent_tiering_discount_pct": 40,
            "unattached_ebs_savings_pct": 100,
        },
    }

    out = Path(__file__).resolve().parents[1] / "data" / "sample_billing.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    total = sum(x["cost_usd"] for x in daily_costs)
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
    print(f"Days={DAYS}, line_items={len(daily_costs)}, util={len(utilization)}")
    print(f"Total cost ~ ${total:,.2f}")


if __name__ == "__main__":
    main()
