"""Integration tests for claims triage FastAPI application."""

import pytest
from fastapi.testclient import TestClient

from triage.api import create_app
from triage.config import TriageConfig
from triage.service import TriageService
from triage.tools.fixture_backend import FixtureToolsBackend

TEST_API_KEY = "test-secret-triage-key-12345"


@pytest.fixture
def client(tmp_path):
    """Create test client backed by isolated temporary databases."""
    triage_db = tmp_path / "triage.db"
    cp_db = tmp_path / "checkpoint.db"
    cfg = TriageConfig(
        TRIAGE_DB_PATH=str(triage_db),
        CHECKPOINT_DB_PATH=str(cp_db),
        TRIAGE_API_KEY=TEST_API_KEY,
        LLM_MODE="oracle",
    )
    tools = FixtureToolsBackend(cases_dir="data/cases")
    svc = TriageService(config=cfg, tools=tools, cases_dir="data/cases")
    app = create_app(config=cfg, service=svc)
    return TestClient(app)


def test_health_endpoint_no_auth_required(client):
    """GET /health returns 200 without requiring API key."""
    res = client.get("/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    # Ensure no secrets leaked
    assert "TRIAGE_API_KEY" not in str(data)
    assert TEST_API_KEY not in str(data)


def test_auth_missing_and_wrong(client):
    """Missing or incorrect X-API-Key header returns 401."""
    # Missing header
    res1 = client.post("/triage", json={"claim_id": "CLM70000001"})
    assert res1.status_code == 401
    assert "Missing" in res1.json()["detail"]

    # Wrong header
    res2 = client.post(
        "/triage",
        headers={"X-API-Key": "incorrect-key"},
        json={"claim_id": "CLM70000001"},
    )
    assert res2.status_code == 401
    assert "Invalid API key" in res2.json()["detail"]


def test_happy_path_fast_track(client):
    """Clean claim CLM70000001 completes FAST_TRACK immediately."""
    headers = {"X-API-Key": TEST_API_KEY}
    res = client.post(
        "/triage",
        headers=headers,
        json={"claim_id": "CLM70000001", "reprocess": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["decision"]["route_recommended"] == "FAST_TRACK"
    assert data["decision"]["route_final"] == "FAST_TRACK"

    # Verify decision is queryable via GET /decisions/{claim_id}
    res_dec = client.get("/decisions/CLM70000001", headers=headers)
    assert res_dec.status_code == 200
    decisions = res_dec.json()
    assert len(decisions) == 1
    assert decisions[0]["claim_id"] == "CLM70000001"


def test_happy_path_pause_and_resume_review(client):
    """Medium risk claim pauses for review, appears in pending, and resumes."""
    headers = {"X-API-Key": TEST_API_KEY}

    # 1. Start triage -> pauses at review
    res1 = client.post(
        "/triage",
        headers=headers,
        json={"claim_id": "CLM70000009"},
    )
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "awaiting_review"
    thread_id = data1["thread_id"]
    assert thread_id.startswith("triage-CLM70000009")
    assert "packet" in data1
    assert data1["packet"]["recommended_route"] == "STANDARD_REVIEW"

    # Verify no raw claim text in response
    assert "submission_text" not in str(data1)
    assert "Inpatient admission without preauth" not in str(data1)

    # 2. Check pending reviews index
    res_pending = client.get("/reviews/pending", headers=headers)
    assert res_pending.status_code == 200
    pending_list = res_pending.json()
    assert any(p["thread_id"] == thread_id for p in pending_list)

    # 3. Resume review via POST /reviews/{thread_id}
    action = {
        "reviewer_id": "rev10001",
        "action": "CONFIRM",
        "reason": "Confirmed standard review due to missing preauth",
    }
    res2 = client.post(f"/reviews/{thread_id}", headers=headers, json=action)
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] == "completed"
    assert data2["decision"]["route_final"] == "STANDARD_REVIEW"

    # 4. Check pending reviews index is now empty of this thread
    res_pending2 = client.get("/reviews/pending", headers=headers)
    assert res_pending2.status_code == 200
    assert not any(p["thread_id"] == thread_id for p in res_pending2.json())


def test_error_status_codes(client):
    """Verify 404, 409, and 422 error codes with no tracebacks leaked."""
    headers = {"X-API-Key": TEST_API_KEY}

    # 404: unknown review thread
    res_404_rev = client.post(
        "/reviews/triage-nonexistent-thread",
        headers=headers,
        json={
            "reviewer_id": "rev10001",
            "action": "CONFIRM",
            "reason": "Valid reason long enough",
        },
    )
    assert res_404_rev.status_code == 404
    assert "Traceback" not in res_404_rev.text

    # 404: nonexistent decision
    res_404_dec = client.get("/decisions/CLM79999999", headers=headers)
    assert res_404_dec.status_code == 404
    assert "Traceback" not in res_404_dec.text

    # Setup a resolved thread for 409
    res_start = client.post(
        "/triage",
        headers=headers,
        json={"claim_id": "CLM70000009"},
    )
    thread_id = res_start.json()["thread_id"]
    valid_action = {
        "reviewer_id": "rev10001",
        "action": "CONFIRM",
        "reason": "Resolving this thread successfully",
    }
    client.post(f"/reviews/{thread_id}", headers=headers, json=valid_action)

    # 409: attempting to resume an already resolved thread
    res_409 = client.post(f"/reviews/{thread_id}", headers=headers, json=valid_action)
    assert res_409.status_code == 409
    assert "already resolved" in res_409.json()["detail"]
    assert "Traceback" not in res_409.text

    # 422: invalid claim_id shape
    res_422_claim = client.post(
        "/triage",
        headers=headers,
        json={"claim_id": "INVALID123"},
    )
    assert res_422_claim.status_code == 422
    assert "Traceback" not in res_422_claim.text

    # 422: invalid review payload (reason too short)
    res_422_rev = client.post(
        f"/reviews/{thread_id}",
        headers=headers,
        json={
            "reviewer_id": "rev10001",
            "action": "CONFIRM",
            "reason": "short",
        },
    )
    assert res_422_rev.status_code == 422
    assert "Traceback" not in res_422_rev.text
