"""Version 5 — FastAPI dashboard for the FinOps agent.

Serves a browser UI that:
- loads spend / anomaly / waste overview charts from analytics
- answers natural-language questions via V4 ``LLMFinOpsAgent`` (MCP tools)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from finops_agent.analytics import (
    detect_anomaly,
    find_underutilized_resources,
    get_cost_breakdown,
)
from finops_agent.data_store import load_dataset
from finops_agent.llm_agent import LLMFinOpsAgent

WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"
INDEX_FILE = WEB_DIR / "templates" / "index.html"

PlannerKind = Literal["auto", "offline", "openai"]


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    planner: PlannerKind = "offline"
    model: str = "gpt-4o-mini"
    lookback_days: int = Field(default=30, ge=7, le=90)


def create_app() -> FastAPI:
    app = FastAPI(
        title="FinOps Agent Dashboard",
        version="0.5.0",
        description="V5 web UI over FinOps MCP tools and analytics",
    )

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        if not INDEX_FILE.exists():
            raise HTTPException(status_code=500, detail="Dashboard index missing")
        return FileResponse(INDEX_FILE)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "finops-dashboard"}

    @app.get("/api/overview")
    async def overview(lookback_days: int = 30) -> dict[str, Any]:
        dataset = load_dataset()
        breakdown = get_cost_breakdown(group_by="service")
        by_region = get_cost_breakdown(group_by="region")
        anomalies = detect_anomaly(
            lookback_days=lookback_days, sensitivity=2.5, group_by="service"
        )
        under = find_underutilized_resources(
            cpu_threshold_pct=20.0, lookback_days=lookback_days
        )
        return {
            "account": {
                "id": dataset.account_id,
                "name": dataset.account_name,
                "provider": dataset.cloud_provider,
                "currency": dataset.currency,
                "period": dataset.period,
            },
            "total_cost_usd": breakdown.total_cost_usd,
            "by_service": [b.model_dump() for b in breakdown.buckets],
            "by_region": [b.model_dump() for b in by_region.buckets],
            "anomalies": {
                "count": len(anomalies.anomalies),
                "summary": anomalies.summary,
                "baseline_mean_usd": anomalies.baseline_mean_usd,
                "items": [a.model_dump() for a in anomalies.anomalies[:8]],
            },
            "underutilized": {
                "count": len(under.resources),
                "total_monthly_savings_usd": under.total_monthly_savings_usd,
                "summary": under.summary,
                "items": [r.model_dump() for r in under.resources[:6]],
            },
        }

    @app.post("/api/ask")
    async def ask(body: AskRequest) -> dict[str, Any]:
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is required")
        try:
            agent = LLMFinOpsAgent(
                planner_kind=body.planner,
                model=body.model,
                lookback_days=body.lookback_days,
            )
            trace = await agent.ask(question)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface agent/MCP failures cleanly
            raise HTTPException(status_code=502, detail=f"Agent failed: {exc}") from exc
        return trace.to_dict()

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(
        "finops_agent.web.app:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
