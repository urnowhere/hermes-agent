"""Cron data access helpers for the Hermes Web Console."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

import cron.jobs as cron_jobs

_CRON_THREAT_PATTERNS = [
    (r"ignore\s+(?:\w+\s+)*(?:previous|all|above|prior)\s+(?:\w+\s+)*instructions", "prompt_injection"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_curl"),
    (r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", "exfil_wget"),
    (r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass)", "read_secrets"),
    (r"authorized_keys", "ssh_backdoor"),
    (r"/etc/sudoers|visudo", "sudoers_mod"),
    (r"rm\s+-rf\s+/", "destructive_root_rm"),
]

_CRON_INVISIBLE_CHARS = {
    "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff",
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",
}


def _scan_cron_prompt(prompt: str) -> str:
    for char in _CRON_INVISIBLE_CHARS:
        if char in prompt:
            return f"Blocked: prompt contains invisible unicode U+{ord(char):04X} (possible injection)."
    for pattern, pattern_id in _CRON_THREAT_PATTERNS:
        if re.search(pattern, prompt, re.IGNORECASE):
            return f"Blocked: prompt matches threat pattern '{pattern_id}'. Cron prompts must not contain injection or exfiltration payloads."
    return ""


class CronService:
    """Thin wrapper around Hermes cron storage and lifecycle helpers."""

    @staticmethod
    def _normalize_skills(skill: str | None = None, skills: Any = None) -> list[str]:
        if skills is None:
            raw_items = [skill] if skill else []
        elif isinstance(skills, str):
            raw_items = [skills]
        else:
            raw_items = list(skills)

        normalized: list[str] = []
        for item in raw_items:
            text = str(item or "").strip()
            if text and text not in normalized:
                normalized.append(text)
        return normalized

    @staticmethod
    def _serialize_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        normalized = dict(job)
        normalized["job_id"] = normalized.get("id")
        normalized["skills"] = CronService._normalize_skills(normalized.get("skill"), normalized.get("skills"))
        normalized["skill"] = normalized["skills"][0] if normalized["skills"] else None
        normalized.setdefault("deliver", "local")
        normalized.setdefault("enabled", True)
        normalized.setdefault(
            "state",
            "scheduled" if normalized.get("enabled", True) else "paused",
        )
        return normalized

    @staticmethod
    def _parse_repeat(repeat: Any) -> int | None:
        if repeat is None:
            return None
        if isinstance(repeat, bool) or not isinstance(repeat, int):
            raise ValueError("The 'repeat' field must be an integer or null.")
        if repeat < 1:
            raise ValueError("The 'repeat' field must be >= 1 when provided.")
        return repeat

    @staticmethod
    def _parse_optional_text(value: Any, *, field_name: str, strip_trailing_slash: bool = False) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"The '{field_name}' field must be a string or null.")
        normalized = value.strip()
        if strip_trailing_slash:
            normalized = normalized.rstrip("/")
        return normalized or None

    def _validate_prompt_and_skills(self, *, prompt: Any, skill: Any = None, skills: Any = None) -> tuple[str, list[str]]:
        if prompt is not None and not isinstance(prompt, str):
            raise ValueError("The 'prompt' field must be a string when provided.")
        canonical_skills = self._normalize_skills(skill if isinstance(skill, str) else None, skills)
        normalized_prompt = prompt if isinstance(prompt, str) else ""
        if normalized_prompt:
            scan_error = _scan_cron_prompt(normalized_prompt)
            if scan_error:
                raise ValueError(scan_error)
        return normalized_prompt, canonical_skills

    def list_jobs(self, *, include_disabled: bool = True) -> dict[str, Any]:
        jobs = [self._serialize_job(job) for job in cron_jobs.list_jobs(include_disabled=include_disabled)]
        return {
            "jobs": jobs,
            "count": len(jobs),
            "include_disabled": include_disabled,
        }

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        return self._serialize_job(cron_jobs.get_job(job_id))

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        schedule = payload.get("schedule")
        if not isinstance(schedule, str) or not schedule.strip():
            raise ValueError("The 'schedule' field must be a non-empty string.")

        prompt, canonical_skills = self._validate_prompt_and_skills(
            prompt=payload.get("prompt"),
            skill=payload.get("skill"),
            skills=payload.get("skills"),
        )
        if not prompt.strip() and not canonical_skills:
            raise ValueError("Provide either a non-empty 'prompt' or at least one skill.")

        name = self._parse_optional_text(payload.get("name"), field_name="name")
        deliver = self._parse_optional_text(payload.get("deliver"), field_name="deliver")
        model = self._parse_optional_text(payload.get("model"), field_name="model")
        provider = self._parse_optional_text(payload.get("provider"), field_name="provider")
        base_url = self._parse_optional_text(payload.get("base_url"), field_name="base_url", strip_trailing_slash=True)
        repeat = self._parse_repeat(payload.get("repeat"))

        job = cron_jobs.create_job(
            prompt=prompt,
            schedule=schedule.strip(),
            name=name,
            repeat=repeat,
            deliver=deliver,
            skills=canonical_skills,
            model=model,
            provider=provider,
            base_url=base_url,
        )
        return self._serialize_job(job) or {}

    def update_job(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        existing = cron_jobs.get_job(job_id)
        if existing is None:
            return None

        updates: dict[str, Any] = {}
        if "prompt" in payload or "skills" in payload or "skill" in payload:
            prompt, canonical_skills = self._validate_prompt_and_skills(
                prompt=payload.get("prompt", existing.get("prompt", "")),
                skill=payload.get("skill", existing.get("skill")),
                skills=payload.get("skills", existing.get("skills")),
            )
            if "prompt" in payload:
                updates["prompt"] = prompt
            if "skills" in payload or "skill" in payload:
                updates["skills"] = canonical_skills
                updates["skill"] = canonical_skills[0] if canonical_skills else None
            effective_prompt = updates.get("prompt", existing.get("prompt", ""))
            effective_skills = updates.get("skills", self._normalize_skills(existing.get("skill"), existing.get("skills")))
            if not str(effective_prompt).strip() and not effective_skills:
                raise ValueError("A cron job must keep either a non-empty prompt or at least one skill.")

        if "name" in payload:
            updates["name"] = self._parse_optional_text(payload.get("name"), field_name="name") or existing.get("name")
        if "deliver" in payload:
            updates["deliver"] = self._parse_optional_text(payload.get("deliver"), field_name="deliver")
        if "model" in payload:
            updates["model"] = self._parse_optional_text(payload.get("model"), field_name="model")
        if "provider" in payload:
            updates["provider"] = self._parse_optional_text(payload.get("provider"), field_name="provider")
        if "base_url" in payload:
            updates["base_url"] = self._parse_optional_text(payload.get("base_url"), field_name="base_url", strip_trailing_slash=True)
        if "repeat" in payload:
            repeat_state = dict(existing.get("repeat") or {})
            repeat_state["times"] = self._parse_repeat(payload.get("repeat"))
            updates["repeat"] = repeat_state
        if "schedule" in payload:
            schedule_value = payload.get("schedule")
            if not isinstance(schedule_value, str) or not schedule_value.strip():
                raise ValueError("The 'schedule' field must be a non-empty string.")
            parsed_schedule = cron_jobs.parse_schedule(schedule_value.strip())
            updates["schedule"] = parsed_schedule
            updates["schedule_display"] = parsed_schedule.get("display", schedule_value.strip())
            if existing.get("state") != "paused":
                updates["enabled"] = True
                updates["state"] = "scheduled"

        if not updates:
            raise ValueError("No updates were provided.")

        updated = cron_jobs.update_job(job_id, updates)
        return self._serialize_job(updated)

    def pause_job(self, job_id: str, *, reason: str | None = None) -> dict[str, Any] | None:
        if reason is not None and not isinstance(reason, str):
            raise ValueError("The 'reason' field must be a string when provided.")
        return self._serialize_job(cron_jobs.pause_job(job_id, reason=reason.strip() or None if isinstance(reason, str) else None))

    def resume_job(self, job_id: str) -> dict[str, Any] | None:
        return self._serialize_job(cron_jobs.resume_job(job_id))

    def run_job(self, job_id: str) -> dict[str, Any] | None:
        return self._serialize_job(cron_jobs.trigger_job(job_id))

    def delete_job(self, job_id: str) -> bool:
        return cron_jobs.remove_job(job_id)

    @staticmethod
    def _history_timestamp(output_file: Path) -> str:
        try:
            return datetime.strptime(output_file.stem, "%Y-%m-%d_%H-%M-%S").astimezone().isoformat()
        except ValueError:
            return datetime.fromtimestamp(output_file.stat().st_mtime).astimezone().isoformat()

    def get_job_history(self, job_id: str, *, limit: int = 20) -> dict[str, Any] | None:
        job = self.get_job(job_id)
        if job is None:
            return None

        if limit < 0:
            raise ValueError("The 'limit' field must be >= 0.")

        output_dir = cron_jobs.OUTPUT_DIR / job_id
        entries: list[dict[str, Any]] = []
        if output_dir.exists():
            files = sorted(
                [path for path in output_dir.iterdir() if path.is_file()],
                key=lambda path: path.name,
                reverse=True,
            )
            for output_file in files[:limit]:
                text = output_file.read_text(encoding="utf-8", errors="replace")
                preview = text[:500]
                entries.append(
                    {
                        "run_id": output_file.stem,
                        "job_id": job_id,
                        "output_file": str(output_file),
                        "filename": output_file.name,
                        "created_at": self._history_timestamp(output_file),
                        "size_bytes": output_file.stat().st_size,
                        "output_preview": preview,
                        "output_truncated": len(text) > len(preview),
                        "output_available": True,
                        "source": "output_file",
                    }
                )

        return {
            "job_id": job_id,
            "history": entries,
            "count": len(entries),
            "latest_run_at": job.get("last_run_at"),
            "latest_status": job.get("last_status"),
            "latest_error": job.get("last_error"),
        }
