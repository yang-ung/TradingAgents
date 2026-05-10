from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tradingagents.dashboard.app import create_dashboard_app
from tradingagents.dashboard.storage import AnalysisRepository


@pytest.mark.unit
def test_repository_tracks_batch_job_lifecycle(tmp_path):
    repository = AnalysisRepository(tmp_path)

    job = repository.create_batch_job(
        tickers=["005930", "NVDA"],
        trade_date="2026-04-30",
        use_hermes_codex_auth=True,
        debug=False,
    )

    assert job["job_id"].startswith("job-")
    assert job["status"] == "queued"
    assert job["tickers"] == ["005930", "NVDA"]
    assert repository.list_batch_jobs()[0]["job_id"] == job["job_id"]

    repository.mark_batch_job_running(job["job_id"])
    running = repository.get_batch_job(job["job_id"])
    assert running["status"] == "running"
    assert running["started_at"]

    summary = {"completed": 2, "failed": [], "run_ids": ["run-a", "run-b"]}
    repository.mark_batch_job_completed(job["job_id"], summary)
    completed = repository.get_batch_job(job["job_id"])
    assert completed["status"] == "completed"
    assert completed["completed"] == 2
    assert completed["failed_count"] == 0
    assert completed["run_ids"] == ["run-a", "run-b"]
    assert completed["finished_at"]

    trace_job = repository.create_batch_job(tickers=["MSFT"], trade_date="2026-04-30")
    repository.mark_batch_job_running(trace_job["job_id"])
    repository.mark_batch_job_completed(
        trace_job["job_id"],
        {"completed": 0, "failed": [{"ticker": "MSFT", "error": "boom", "traceback": "internal stack"}], "run_ids": []},
    )
    sanitized = repository.get_batch_job(trace_job["job_id"])
    assert sanitized["summary"]["failed"] == [{"ticker": "MSFT", "error": "boom"}]
    assert "traceback" not in str(sanitized["summary"]).lower()


@pytest.mark.unit
def test_repository_tracks_failed_batch_job(tmp_path):
    repository = AnalysisRepository(tmp_path)
    job = repository.create_batch_job(tickers=["BAD"], trade_date="2026-04-30")

    repository.mark_batch_job_running(job["job_id"])
    repository.mark_batch_job_failed(job["job_id"], "boom")

    failed = repository.get_batch_job(job["job_id"])
    assert failed["status"] == "failed"
    assert failed["error"] == "boom"
    assert failed["finished_at"]


@pytest.mark.unit
def test_dashboard_batch_job_api_runs_in_background_with_injected_runner(tmp_path):
    calls = []

    def fake_runner(tickers, trade_date, *, artifact_dir, use_hermes_codex_auth=False, debug=False):
        calls.append(
            {
                "tickers": list(tickers),
                "trade_date": trade_date,
                "artifact_dir": str(artifact_dir),
                "use_hermes_codex_auth": use_hermes_codex_auth,
                "debug": debug,
            }
        )
        return {"artifact_dir": str(artifact_dir), "trade_date": trade_date, "tickers": list(tickers), "completed": 2, "failed": [], "run_ids": ["run-a", "run-b"]}

    client = TestClient(create_dashboard_app(tmp_path, batch_runner=fake_runner))

    response = client.post(
        "/api/batch-jobs",
        json={
            "tickers": "005930, NVDA",
            "trade_date": "2026-04-30",
            "use_hermes_codex_auth": True,
            "debug": True,
        },
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]
    assert response.json()["status"] in {"queued", "running", "completed"}

    deadline = time.time() + 2
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/batch-jobs/{job_id}").json()
        if job["status"] == "completed":
            break
        time.sleep(0.02)

    assert job["status"] == "completed"
    assert job["completed"] == 2
    assert job["run_ids"] == ["run-a", "run-b"]
    assert calls == [
        {
            "tickers": ["005930", "NVDA"],
            "trade_date": "2026-04-30",
            "artifact_dir": str(tmp_path),
            "use_hermes_codex_auth": True,
            "debug": True,
        }
    ]


@pytest.mark.unit
def test_dashboard_batch_job_api_validates_inputs_and_hides_manual_batch_ui(tmp_path):
    client = TestClient(create_dashboard_app(tmp_path, batch_runner=lambda *args, **kwargs: {}))

    page = client.get("/")
    assert page.status_code == 200
    assert "새 배치 실행" not in page.text
    assert "종목 입력" not in page.text
    assert "실행 상태" not in page.text
    assert "batch-form" not in page.text
    assert "refreshJobs" not in page.text
    assert "/api/batch-jobs" not in page.text
    assert "정해진 시간에 자동으로 갱신" in page.text

    missing_tickers = client.post("/api/batch-jobs", json={"tickers": "", "trade_date": "2026-04-30"})
    assert missing_tickers.status_code == 400

    missing_date = client.post("/api/batch-jobs", json={"tickers": "NVDA", "trade_date": ""})
    assert missing_date.status_code == 400

    invalid_date = client.post("/api/batch-jobs", json={"tickers": "NVDA", "trade_date": "2026-99-99"})
    assert invalid_date.status_code == 400

    invalid_ticker = client.post("/api/batch-jobs", json={"tickers": "../secret", "trade_date": "2026-04-30"})
    assert invalid_ticker.status_code == 400

    invalid_bool = client.post(
        "/api/batch-jobs",
        json={"tickers": "NVDA", "trade_date": "2026-04-30", "debug": "maybe"},
    )
    assert invalid_bool.status_code == 400


def test_dashboard_batch_job_parses_string_false_as_false(tmp_path):
    calls = []

    def fake_runner(tickers, trade_date, *, artifact_dir, use_hermes_codex_auth=False, debug=False):
        calls.append({"use_hermes_codex_auth": use_hermes_codex_auth, "debug": debug})
        return {"completed": 1, "failed": [], "run_ids": ["run-a"]}

    client = TestClient(create_dashboard_app(tmp_path, batch_runner=fake_runner))
    response = client.post(
        "/api/batch-jobs",
        json={"tickers": "NVDA", "trade_date": "2026-04-30", "use_hermes_codex_auth": "false", "debug": "false"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.time() + 2
    while time.time() < deadline:
        if client.get(f"/api/batch-jobs/{job_id}").json()["status"] == "completed":
            break
        time.sleep(0.02)

    assert calls == [{"use_hermes_codex_auth": False, "debug": False}]


@pytest.mark.unit
def test_dashboard_recovers_interrupted_batch_jobs_on_startup(tmp_path):
    repository = AnalysisRepository(tmp_path)
    queued = repository.create_batch_job(tickers=["NVDA"], trade_date="2026-04-30")
    running = repository.create_batch_job(tickers=["005930"], trade_date="2026-04-30")
    repository.mark_batch_job_running(running["job_id"])

    client = TestClient(create_dashboard_app(tmp_path, batch_runner=lambda *args, **kwargs: {}))

    assert client.get(f"/api/batch-jobs/{queued['job_id']}").json()["status"] == "failed"
    assert "interrupted" in client.get(f"/api/batch-jobs/{running['job_id']}").json()["error"]


@pytest.mark.unit
def test_dashboard_rejects_too_many_active_batch_jobs(tmp_path, monkeypatch):
    import tradingagents.dashboard.app as dashboard_app

    monkeypatch.setattr(dashboard_app, "MAX_ACTIVE_BATCH_JOBS", 1)

    def slow_runner(*args, **kwargs):
        time.sleep(0.5)
        return {"completed": 1, "failed": [], "run_ids": ["run-a"]}

    client = TestClient(create_dashboard_app(tmp_path, batch_runner=slow_runner))
    first = client.post("/api/batch-jobs", json={"tickers": "NVDA", "trade_date": "2026-04-30"})
    assert first.status_code == 202

    second = client.post("/api/batch-jobs", json={"tickers": "005930", "trade_date": "2026-04-30"})
    assert second.status_code == 429


@pytest.mark.unit
def test_dashboard_batch_job_api_records_runner_failure(tmp_path):
    def failing_runner(*args, **kwargs):
        raise RuntimeError("batch failed")

    client = TestClient(create_dashboard_app(tmp_path, batch_runner=failing_runner))
    response = client.post("/api/batch-jobs", json={"tickers": "NVDA", "trade_date": "2026-04-30"})
    assert response.status_code == 202
    job_id = response.json()["job_id"]

    deadline = time.time() + 2
    job = None
    while time.time() < deadline:
        job = client.get(f"/api/batch-jobs/{job_id}").json()
        if job["status"] == "failed":
            break
        time.sleep(0.02)

    assert job["status"] == "failed"
    assert "batch failed" in job["error"]
