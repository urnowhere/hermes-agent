"""Tests for the web console cron API and cron service."""

from __future__ import annotations

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

import cron.jobs as cron_jobs
from gateway.web_console.api.cron import CRON_SERVICE_APP_KEY
from gateway.web_console.routes import register_web_console_routes
from gateway.web_console.services.cron_service import CronService


class FakeCronService:
    def __init__(self) -> None:
        self.jobs = {
            "job-1": {
                "job_id": "job-1",
                "id": "job-1",
                "name": "Morning report",
                "prompt": "Summarize overnight alerts",
                "skills": ["blogwatcher"],
                "skill": "blogwatcher",
                "schedule": {"kind": "interval", "minutes": 60, "display": "every 60m"},
                "schedule_display": "every 60m",
                "repeat": {"times": None, "completed": 2},
                "deliver": "local",
                "enabled": True,
                "state": "scheduled",
                "next_run_at": "2026-03-30T10:00:00+00:00",
                "last_run_at": "2026-03-30T09:00:00+00:00",
                "last_status": "ok",
                "last_error": None,
                "paused_at": None,
                "paused_reason": None,
            }
        }
        self.history = {
            "job-1": {
                "job_id": "job-1",
                "count": 1,
                "latest_run_at": "2026-03-30T09:00:00+00:00",
                "latest_status": "ok",
                "latest_error": None,
                "history": [
                    {
                        "run_id": "2026-03-30_09-00-00",
                        "job_id": "job-1",
                        "filename": "2026-03-30_09-00-00.md",
                        "output_file": "/tmp/job-1/2026-03-30_09-00-00.md",
                        "created_at": "2026-03-30T09:00:00+00:00",
                        "size_bytes": 12,
                        "output_preview": "all good",
                        "output_truncated": False,
                        "output_available": True,
                        "source": "output_file",
                    }
                ],
            }
        }

    def list_jobs(self, *, include_disabled: bool = True):
        jobs = list(self.jobs.values())
        if not include_disabled:
            jobs = [job for job in jobs if job.get("enabled", True)]
        return {"jobs": jobs, "count": len(jobs), "include_disabled": include_disabled}

    def get_job(self, job_id: str):
        return self.jobs.get(job_id)

    def create_job(self, payload):
        if not payload.get("schedule"):
            raise ValueError("The 'schedule' field must be a non-empty string.")
        if payload.get("schedule") == "bad":
            raise ValueError("Invalid schedule")
        job = {
            "job_id": "job-2",
            "id": "job-2",
            "name": payload.get("name") or "Created job",
            "prompt": payload.get("prompt", ""),
            "skills": payload.get("skills") or [],
            "skill": (payload.get("skills") or [None])[0],
            "schedule": {"kind": "interval", "minutes": 30, "display": payload["schedule"]},
            "schedule_display": payload["schedule"],
            "repeat": {"times": payload.get("repeat"), "completed": 0},
            "deliver": payload.get("deliver") or "local",
            "enabled": True,
            "state": "scheduled",
            "next_run_at": "2026-03-30T11:00:00+00:00",
            "last_run_at": None,
            "last_status": None,
            "last_error": None,
            "paused_at": None,
            "paused_reason": None,
        }
        self.jobs[job["job_id"]] = job
        self.history[job["job_id"]] = {
            "job_id": job["job_id"],
            "count": 0,
            "latest_run_at": None,
            "latest_status": None,
            "latest_error": None,
            "history": [],
        }
        return job

    def update_job(self, job_id: str, payload):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        if payload.get("schedule") == "bad":
            raise ValueError("Invalid schedule")
        if "name" in payload:
            job["name"] = payload["name"]
        if "prompt" in payload:
            job["prompt"] = payload["prompt"]
        if "schedule" in payload:
            job["schedule_display"] = payload["schedule"]
        return job

    def run_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job["next_run_at"] = "2026-03-30T09:30:00+00:00"
        return job

    def pause_job(self, job_id: str, *, reason: str | None = None):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job["enabled"] = False
        job["state"] = "paused"
        job["paused_reason"] = reason
        return job

    def resume_job(self, job_id: str):
        job = self.jobs.get(job_id)
        if job is None:
            return None
        job["enabled"] = True
        job["state"] = "scheduled"
        job["paused_reason"] = None
        return job

    def delete_job(self, job_id: str):
        return self.jobs.pop(job_id, None) is not None

    def get_job_history(self, job_id: str, *, limit: int = 20):
        payload = self.history.get(job_id)
        if payload is None:
            return None
        copied = dict(payload)
        copied["history"] = copied["history"][:limit]
        copied["count"] = len(copied["history"])
        return copied


class TestCronApi:
    @staticmethod
    async def _make_client(service: FakeCronService) -> TestClient:
        app = web.Application()
        app[CRON_SERVICE_APP_KEY] = service
        register_web_console_routes(app)
        client = TestClient(TestServer(app))
        await client.start_server()
        return client

    @pytest.mark.asyncio
    async def test_cron_job_crud_and_history_routes(self):
        client = await self._make_client(FakeCronService())
        try:
            list_resp = await client.get("/api/gui/cron/jobs")
            assert list_resp.status == 200
            list_payload = await list_resp.json()
            assert list_payload["ok"] is True
            assert list_payload["count"] == 1
            assert list_payload["jobs"][0]["job_id"] == "job-1"

            create_resp = await client.post(
                "/api/gui/cron/jobs",
                json={
                    "name": "Lunch report",
                    "prompt": "Summarize the morning",
                    "schedule": "every 30m",
                    "deliver": "local",
                    "skills": ["blogwatcher"],
                },
            )
            assert create_resp.status == 200
            create_payload = await create_resp.json()
            assert create_payload["ok"] is True
            assert create_payload["job"]["job_id"] == "job-2"
            assert create_payload["job"]["name"] == "Lunch report"

            detail_resp = await client.get("/api/gui/cron/jobs/job-1")
            assert detail_resp.status == 200
            detail_payload = await detail_resp.json()
            assert detail_payload["job"]["schedule_display"] == "every 60m"
            assert detail_payload["job"]["last_status"] == "ok"

            patch_resp = await client.patch(
                "/api/gui/cron/jobs/job-1",
                json={"name": "Updated report", "prompt": "Use new instructions", "schedule": "every 2h"},
            )
            assert patch_resp.status == 200
            patch_payload = await patch_resp.json()
            assert patch_payload["job"]["name"] == "Updated report"
            assert patch_payload["job"]["prompt"] == "Use new instructions"
            assert patch_payload["job"]["schedule_display"] == "every 2h"

            run_resp = await client.post("/api/gui/cron/jobs/job-1/run")
            assert run_resp.status == 200
            run_payload = await run_resp.json()
            assert run_payload["queued"] is True
            assert run_payload["job"]["next_run_at"] == "2026-03-30T09:30:00+00:00"

            pause_resp = await client.post("/api/gui/cron/jobs/job-1/pause", json={"reason": "maintenance"})
            assert pause_resp.status == 200
            pause_payload = await pause_resp.json()
            assert pause_payload["job"]["state"] == "paused"
            assert pause_payload["job"]["paused_reason"] == "maintenance"

            resume_resp = await client.post("/api/gui/cron/jobs/job-1/resume")
            assert resume_resp.status == 200
            resume_payload = await resume_resp.json()
            assert resume_payload["job"]["state"] == "scheduled"
            assert resume_payload["job"]["enabled"] is True

            history_resp = await client.get("/api/gui/cron/jobs/job-1/history?limit=1")
            assert history_resp.status == 200
            history_payload = await history_resp.json()
            assert history_payload["ok"] is True
            assert history_payload["job_id"] == "job-1"
            assert history_payload["count"] == 1
            assert history_payload["history"][0]["output_preview"] == "all good"

            delete_resp = await client.delete("/api/gui/cron/jobs/job-2")
            assert delete_resp.status == 200
            delete_payload = await delete_resp.json()
            assert delete_payload == {"ok": True, "job_id": "job-2", "deleted": True}
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_cron_api_structured_errors(self):
        client = await self._make_client(FakeCronService())
        try:
            invalid_json_resp = await client.post(
                "/api/gui/cron/jobs",
                data="not json",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json_resp.status == 400
            invalid_json_payload = await invalid_json_resp.json()
            assert invalid_json_payload["error"]["code"] == "invalid_json"

            invalid_job_resp = await client.post("/api/gui/cron/jobs", json={"prompt": "Hello"})
            assert invalid_job_resp.status == 400
            invalid_job_payload = await invalid_job_resp.json()
            assert invalid_job_payload["error"]["code"] == "invalid_job"

            bad_update_resp = await client.patch("/api/gui/cron/jobs/job-1", json={"schedule": "bad"})
            assert bad_update_resp.status == 400
            bad_update_payload = await bad_update_resp.json()
            assert bad_update_payload["error"]["code"] == "invalid_job"

            missing_resp = await client.get("/api/gui/cron/jobs/missing")
            assert missing_resp.status == 404
            missing_payload = await missing_resp.json()
            assert missing_payload["error"]["code"] == "job_not_found"

            missing_delete_resp = await client.delete("/api/gui/cron/jobs/missing")
            assert missing_delete_resp.status == 404
            missing_delete_payload = await missing_delete_resp.json()
            assert missing_delete_payload["error"]["code"] == "job_not_found"

            invalid_limit_resp = await client.get("/api/gui/cron/jobs/job-1/history?limit=abc")
            assert invalid_limit_resp.status == 400
            invalid_limit_payload = await invalid_limit_resp.json()
            assert invalid_limit_payload["error"]["code"] == "invalid_pagination"
        finally:
            await client.close()


class TestCronService:
    def test_real_cron_service_uses_cron_storage_and_output_history(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
        monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
        monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")

        service = CronService()
        created = service.create_job({"prompt": "Summarize logs", "schedule": "every 1h", "name": "Ops summary"})

        cron_jobs.save_job_output(created["job_id"], "First output line\nSecond output line")
        cron_jobs.mark_job_run(created["job_id"], success=True)
        cron_jobs.save_job_output(created["job_id"], "Newest output")

        jobs_payload = service.list_jobs()
        assert jobs_payload["count"] == 1
        assert jobs_payload["jobs"][0]["name"] == "Ops summary"

        job_detail = service.get_job(created["job_id"])
        assert job_detail is not None
        assert job_detail["schedule_display"] == "every 60m"
        assert job_detail["last_status"] == "ok"

        history = service.get_job_history(created["job_id"], limit=10)
        assert history is not None
        assert history["job_id"] == created["job_id"]
        assert history["count"] == 1 or history["count"] == 2
        assert history["history"][0]["output_available"] is True
        assert history["history"][0]["filename"].endswith(".md")
        assert history["history"][0]["source"] == "output_file"
        assert history["history"][0]["output_preview"]
