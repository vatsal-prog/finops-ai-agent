"""Load and query structured cloud billing / utilization datasets."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from finops_agent.models import BillingDataset, CostLineItem, UtilizationPoint

DEFAULT_DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "sample_billing.json"


def _parse_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


@lru_cache(maxsize=4)
def load_dataset(path: str | None = None) -> BillingDataset:
    data_path = Path(path) if path else DEFAULT_DATA_PATH
    with data_path.open() as f:
        raw = json.load(f)
    return BillingDataset.model_validate(raw)


def clear_dataset_cache() -> None:
    load_dataset.cache_clear()


def filter_costs(
    dataset: BillingDataset,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    service: str | None = None,
    region: str | None = None,
    resource_id: str | None = None,
    team: str | None = None,
    env: str | None = None,
) -> list[CostLineItem]:
    start = _parse_date(start_date) if start_date else None
    end = _parse_date(end_date) if end_date else None
    rows: list[CostLineItem] = []
    for item in dataset.daily_costs:
        d = _parse_date(item.date)
        if start and d < start:
            continue
        if end and d > end:
            continue
        if service and item.service.lower() != service.lower():
            continue
        if region and item.region.lower() != region.lower():
            continue
        if resource_id and item.resource_id != resource_id:
            continue
        if team and item.tags.get("team", "").lower() != team.lower():
            continue
        if env and item.tags.get("env", "").lower() != env.lower():
            continue
        rows.append(item)
    return rows


def filter_utilization(
    dataset: BillingDataset,
    *,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    resource_id: str | None = None,
    service: str | None = None,
) -> list[UtilizationPoint]:
    start = _parse_date(start_date) if start_date else None
    end = _parse_date(end_date) if end_date else None
    rows: list[UtilizationPoint] = []
    for item in dataset.utilization:
        d = _parse_date(item.date)
        if start and d < start:
            continue
        if end and d > end:
            continue
        if resource_id and item.resource_id != resource_id:
            continue
        if service and item.service.lower() != service.lower():
            continue
        rows.append(item)
    return rows


def dataset_date_bounds(dataset: BillingDataset) -> tuple[date, date]:
    return _parse_date(dataset.period["start"]), _parse_date(dataset.period["end"])


def resolve_window(
    dataset: BillingDataset,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    lookback_days: int | None = None,
) -> tuple[date, date]:
    data_start, data_end = dataset_date_bounds(dataset)
    end = _parse_date(end_date) if end_date else data_end
    if start_date:
        start = _parse_date(start_date)
    elif lookback_days:
        start = end - timedelta(days=lookback_days - 1)
    else:
        start = data_start
    if start < data_start:
        start = data_start
    if end > data_end:
        end = data_end
    return start, end


def prior_window(start: date, end: date) -> tuple[date, date]:
    length = (end - start).days + 1
    prior_end = start - timedelta(days=1)
    prior_start = prior_end - timedelta(days=length - 1)
    return prior_start, prior_end
