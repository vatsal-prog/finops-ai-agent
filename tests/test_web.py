"""Tests for Version 5 FinOps web dashboard API."""

from __future__ import annotations

from fastapi.testclient import TestClient

from finops_agent.web.app import create_app


def test_health_and_index():
    client = TestClient(create_app())
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    page = client.get("/")
    assert page.status_code == 200
    assert "FINOPS" in page.text
    assert "/static/app.js" in page.text


def test_overview_endpoint():
    client = TestClient(create_app())
    res = client.get("/api/overview")
    assert res.status_code == 200
    data = res.json()
    assert data["total_cost_usd"] > 0
    assert data["by_service"]
    assert data["account"]["name"] == "acme-production"
    assert "anomalies" in data
    assert "underutilized" in data


def test_ask_endpoint_offline(monkeypatch):
    client = TestClient(create_app())
    res = client.post(
        "/api/ask",
        json={
            "question": "Where is our cloud money going by service?",
            "planner": "offline",
        },
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["planner"] == "offline"
    assert data["tool_steps"]
    assert data["tool_steps"][0]["name"] == "get_cost_breakdown"
    assert data["answer"]


def test_ask_rejects_empty_question():
    client = TestClient(create_app())
    res = client.post("/api/ask", json={"question": "   ", "planner": "offline"})
    assert res.status_code == 422 or res.status_code == 400
