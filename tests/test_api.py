from fastapi.testclient import TestClient

from fortune_intel.api import create_app
from fortune_intel.seed import seed_demo


def test_search_api_and_stats(tmp_path):
    app = create_app(tmp_path / "api.db")
    seed_demo(app.state.repository)
    client = TestClient(app)

    response = client.get("/api/jobs", params={"q": "data", "tier": "A"})
    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["company_name"] == "Northstar Systems"

    stats = client.get("/api/stats").json()
    assert stats["active_jobs"] == 4
    assert stats["companies"] == 4
    assert stats["companies_with_successful_job_fetches"] == 0
    assert stats["companies_with_current_job_fetches"] == 0
    assert stats["jobs_with_evidence"] == 3


def test_invalid_tier_is_rejected(tmp_path):
    client = TestClient(create_app(tmp_path / "api.db"))
    assert client.get("/api/jobs?tier=Z").status_code == 422
